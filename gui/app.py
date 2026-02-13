#!/usr/bin/env python3
"""VEX Corpus Generator - PyQt6 GUI Application."""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# Windows console encoding fix
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent.parent))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QProgressBar, QFileDialog,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QGroupBox, QSpinBox, QComboBox, QLineEdit,
    QStatusBar, QToolBar, QMessageBox, QCheckBox, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QAction, QColor

from pipeline.intake import IntakePipeline, VEXSample, ProcessingStage


class ProcessingWorker(QThread):
    """Background worker for async pipeline processing."""

    progress = pyqtSignal(str, int, int)  # stage, current, total
    sample_complete = pyqtSignal(object)  # VEXSample
    error = pyqtSignal(str, str)  # source, message
    finished = pyqtSignal(list)  # processed samples
    log = pyqtSignal(str)  # log message

    def __init__(self, pipeline: IntakePipeline, sources: list):
        super().__init__()
        self.pipeline = pipeline
        self.sources = sources  # List of (type, path) tuples
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Connect callbacks
            self.pipeline.on_progress(
                lambda stage, cur, tot: self.progress.emit(stage, cur, tot)
            )
            self.pipeline.on_sample_complete(
                lambda s: self.sample_complete.emit(s)
            )
            self.pipeline.on_error(
                lambda src, e: self.error.emit(src, str(e))
            )

            all_samples = []

            # Ingest from all sources
            for source_type, source_path in self.sources:
                if self._cancelled:
                    break

                self.log.emit(f"Ingesting from {source_path}...")

                if source_type == "file":
                    samples = loop.run_until_complete(
                        self.pipeline.ingest_file(Path(source_path))
                    )
                elif source_type == "directory":
                    samples = loop.run_until_complete(
                        self.pipeline.ingest_directory(Path(source_path))
                    )
                elif source_type == "code":
                    sample = loop.run_until_complete(
                        self.pipeline.ingest_code(source_path)
                    )
                    samples = [sample]
                else:
                    continue

                all_samples.extend(samples)
                self.log.emit(f"  Found {len(samples)} samples")

            if self._cancelled:
                self.log.emit("Processing cancelled.")
                self.finished.emit([])
                return

            self.log.emit(f"\nTotal samples to process: {len(all_samples)}")

            # Process through pipeline
            if all_samples:
                self.log.emit("\nStarting Tier 1 classification...")
                processed = loop.run_until_complete(
                    self.pipeline.process_all(all_samples)
                )
                self.log.emit(f"\nProcessing complete: {len(processed)} samples")
                self.finished.emit(processed)
            else:
                self.log.emit("No samples found to process.")
                self.finished.emit([])

        except Exception as e:
            self.error.emit("pipeline", str(e))
            self.finished.emit([])
        finally:
            loop.close()


class SampleTableWidget(QTableWidget):
    """Table for displaying VEX samples."""

    def __init__(self):
        super().__init__()
        self.setup_table()

    def setup_table(self):
        self.setColumnCount(7)
        self.setHorizontalHeaderLabels([
            "ID", "Context", "Confidence", "Topic", "Complexity", "Stage", "Flagged"
        ])

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

    def add_sample(self, sample: VEXSample):
        row = self.rowCount()
        self.insertRow(row)

        self.setItem(row, 0, QTableWidgetItem(sample.id[:16]))
        self.setItem(row, 1, QTableWidgetItem(sample.context or "?"))
        self.setItem(row, 2, QTableWidgetItem(f"{sample.context_confidence:.2f}"))
        self.setItem(row, 3, QTableWidgetItem(sample.topic or "?"))
        self.setItem(row, 4, QTableWidgetItem(sample.complexity or "?"))
        self.setItem(row, 5, QTableWidgetItem(sample.stage.value))

        flagged_item = QTableWidgetItem("YES" if sample.flagged_for_review else "")
        if sample.flagged_for_review:
            flagged_item.setBackground(QColor(255, 200, 200))
        self.setItem(row, 6, flagged_item)

    def clear_samples(self):
        self.setRowCount(0)


class CodePreviewWidget(QWidget):
    """Widget for previewing VEX code and generated content."""

    def __init__(self):
        super().__init__()
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Code display
        code_group = QGroupBox("VEX Code")
        code_layout = QVBoxLayout(code_group)
        self.code_edit = QTextEdit()
        self.code_edit.setReadOnly(True)
        self.code_edit.setFont(QFont("Consolas", 10))
        code_layout.addWidget(self.code_edit)

        # Generated prompt
        prompt_group = QGroupBox("Generated Prompt")
        prompt_layout = QVBoxLayout(prompt_group)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setReadOnly(True)
        self.prompt_edit.setMaximumHeight(80)
        prompt_layout.addWidget(self.prompt_edit)

        # Explanation
        explain_group = QGroupBox("Explanation")
        explain_layout = QVBoxLayout(explain_group)
        self.explain_edit = QTextEdit()
        self.explain_edit.setReadOnly(True)
        explain_layout.addWidget(self.explain_edit)

        layout.addWidget(code_group, 2)
        layout.addWidget(prompt_group, 1)
        layout.addWidget(explain_group, 2)

    def show_sample(self, sample: VEXSample):
        self.code_edit.setPlainText(sample.code)
        self.prompt_edit.setPlainText(sample.prompt or "(Not generated)")
        self.explain_edit.setPlainText(sample.explanation or "(Not generated)")


class StatsWidget(QWidget):
    """Widget for displaying processing statistics."""

    def __init__(self):
        super().__init__()
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Stats grid
        stats_group = QGroupBox("Statistics")
        stats_layout = QVBoxLayout(stats_group)

        self.stats_labels = {}
        stats_items = [
            ("total", "Total Samples"),
            ("pending", "Pending"),
            ("complete", "Complete"),
            ("flagged", "Flagged for Review"),
            ("with_prompts", "With Prompts"),
        ]

        for key, label in stats_items:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{label}:"))
            value_label = QLabel("0")
            value_label.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
            row.addWidget(value_label)
            row.addStretch()
            stats_layout.addLayout(row)
            self.stats_labels[key] = value_label

        layout.addWidget(stats_group)

        # Context breakdown
        context_group = QGroupBox("By Context")
        context_layout = QVBoxLayout(context_group)
        self.context_table = QTableWidget()
        self.context_table.setColumnCount(2)
        self.context_table.setHorizontalHeaderLabels(["Context", "Count"])
        self.context_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        context_layout.addWidget(self.context_table)

        layout.addWidget(context_group)
        layout.addStretch()

    def update_stats(self, stats: dict):
        self.stats_labels["total"].setText(str(stats.get("total", 0)))
        self.stats_labels["pending"].setText(
            str(stats.get("by_stage", {}).get("pending", 0))
        )
        self.stats_labels["complete"].setText(
            str(stats.get("by_stage", {}).get("complete", 0))
        )
        self.stats_labels["flagged"].setText(str(stats.get("flagged_for_review", 0)))
        self.stats_labels["with_prompts"].setText(str(stats.get("with_prompts", 0)))

        # Update context table
        by_context = stats.get("by_context", {})
        self.context_table.setRowCount(len(by_context))
        for i, (ctx, count) in enumerate(by_context.items()):
            self.context_table.setItem(i, 0, QTableWidgetItem(ctx))
            self.context_table.setItem(i, 1, QTableWidgetItem(str(count)))


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.pipeline = IntakePipeline()
        self.samples: dict[str, VEXSample] = {}
        self.worker: Optional[ProcessingWorker] = None

        self.setup_ui()
        self.setup_menu()
        self.setup_toolbar()
        self.setup_statusbar()

    def setup_ui(self):
        self.setWindowTitle("VEX Corpus Generator")
        self.setMinimumSize(1200, 800)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel - input and controls
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # Input section
        input_group = QGroupBox("Input")
        input_layout = QVBoxLayout(input_group)

        # Directory input
        dir_row = QHBoxLayout()
        self.dir_input = QLineEdit()
        self.dir_input.setPlaceholderText("Select directory with VEX files...")
        dir_row.addWidget(self.dir_input)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_directory)
        dir_row.addWidget(browse_btn)
        input_layout.addLayout(dir_row)

        # Code input
        input_layout.addWidget(QLabel("Or paste VEX code:"))
        self.code_input = QTextEdit()
        self.code_input.setPlaceholderText("Paste VEX code here...")
        self.code_input.setMaximumHeight(150)
        self.code_input.setFont(QFont("Consolas", 10))
        input_layout.addWidget(self.code_input)

        # Add code button
        add_code_btn = QPushButton("Add Code Sample")
        add_code_btn.clicked.connect(self.add_code_sample)
        input_layout.addWidget(add_code_btn)

        left_layout.addWidget(input_group)

        # Processing options
        options_group = QGroupBox("Processing Options")
        options_layout = QVBoxLayout(options_group)

        tier1_row = QHBoxLayout()
        tier1_row.addWidget(QLabel("Tier 1 Concurrency:"))
        self.tier1_spin = QSpinBox()
        self.tier1_spin.setRange(1, 10)
        self.tier1_spin.setValue(5)
        tier1_row.addWidget(self.tier1_spin)
        tier1_row.addStretch()
        options_layout.addLayout(tier1_row)

        tier2_row = QHBoxLayout()
        tier2_row.addWidget(QLabel("Tier 2 Concurrency:"))
        self.tier2_spin = QSpinBox()
        self.tier2_spin.setRange(1, 5)
        self.tier2_spin.setValue(2)
        tier2_row.addWidget(self.tier2_spin)
        tier2_row.addStretch()
        options_layout.addLayout(tier2_row)

        self.skip_flagged_check = QCheckBox("Skip Tier 2 for flagged samples")
        self.skip_flagged_check.setChecked(True)
        options_layout.addWidget(self.skip_flagged_check)

        left_layout.addWidget(options_group)

        # Action buttons
        actions_layout = QHBoxLayout()

        self.process_btn = QPushButton("Process All")
        self.process_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.process_btn.clicked.connect(self.start_processing)
        actions_layout.addWidget(self.process_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_processing)
        actions_layout.addWidget(self.cancel_btn)

        left_layout.addLayout(actions_layout)

        # Progress
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)

        self.progress_label = QLabel("Ready")
        progress_layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)

        left_layout.addWidget(progress_group)

        # Log
        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)
        left_layout.addWidget(log_group)

        splitter.addWidget(left_panel)

        # Right panel - results
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # Tabs
        tabs = QTabWidget()

        # Samples tab
        samples_tab = QWidget()
        samples_layout = QVBoxLayout(samples_tab)
        self.sample_table = SampleTableWidget()
        self.sample_table.cellClicked.connect(self.on_sample_selected)
        samples_layout.addWidget(self.sample_table)
        tabs.addTab(samples_tab, "Samples")

        # Preview tab
        self.preview_widget = CodePreviewWidget()
        tabs.addTab(self.preview_widget, "Preview")

        # Stats tab
        self.stats_widget = StatsWidget()
        tabs.addTab(self.stats_widget, "Statistics")

        right_layout.addWidget(tabs)

        splitter.addWidget(right_panel)
        splitter.setSizes([400, 800])

        layout.addWidget(splitter)

    def setup_menu(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        open_action = QAction("Open Directory...", self)
        open_action.triggered.connect(self.browse_directory)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        export_corpus_action = QAction("Export Corpus...", self)
        export_corpus_action.triggered.connect(self.export_corpus)
        file_menu.addAction(export_corpus_action)

        export_training_action = QAction("Export Training Data...", self)
        export_training_action.triggered.connect(self.export_training)
        file_menu.addAction(export_training_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Help menu
        help_menu = menubar.addMenu("Help")

        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def setup_toolbar(self):
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        toolbar.addAction("Open", self.browse_directory)
        toolbar.addSeparator()
        toolbar.addAction("Process", self.start_processing)
        toolbar.addAction("Clear", self.clear_all)
        toolbar.addSeparator()
        toolbar.addAction("Export", self.export_corpus)

    def setup_statusbar(self):
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.statusbar.showMessage("Ready - Ollama backend required")

    def log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    def browse_directory(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select VEX Directory",
            str(Path.home())
        )
        if dir_path:
            self.dir_input.setText(dir_path)
            self.log(f"Selected directory: {dir_path}")

    def add_code_sample(self):
        code = self.code_input.toPlainText().strip()
        if not code:
            QMessageBox.warning(self, "No Code", "Please paste VEX code first.")
            return

        # Create sample directly
        sample = VEXSample(id="", code=code, source_file="direct_input")
        self.samples[sample.id] = sample
        self.sample_table.add_sample(sample)
        self.pipeline.samples[sample.id] = sample
        self.pipeline.processed_hashes.add(sample.hash)

        self.code_input.clear()
        self.log(f"Added code sample: {sample.id}")
        self.update_stats()

    def start_processing(self):
        sources = []

        # Check directory input
        dir_path = self.dir_input.text().strip()
        if dir_path and Path(dir_path).exists():
            sources.append(("directory", dir_path))

        # Check for pending samples in pipeline
        pending_count = sum(
            1 for s in self.pipeline.samples.values()
            if s.stage == ProcessingStage.PENDING
        )

        if not sources and pending_count == 0:
            QMessageBox.warning(
                self, "No Input",
                "Please select a directory or add code samples first."
            )
            return

        # Update pipeline settings
        self.pipeline.tier1_concurrency = self.tier1_spin.value()
        self.pipeline.tier2_concurrency = self.tier2_spin.value()

        # Start worker
        self.worker = ProcessingWorker(self.pipeline, sources)
        self.worker.progress.connect(self.on_progress)
        self.worker.sample_complete.connect(self.on_sample_complete)
        self.worker.error.connect(self.on_error)
        self.worker.finished.connect(self.on_finished)
        self.worker.log.connect(self.log)

        self.process_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)

        self.log("Starting processing...")
        self.worker.start()

    def cancel_processing(self):
        if self.worker:
            self.worker.cancel()
            self.log("Cancellation requested...")

    def on_progress(self, stage: str, current: int, total: int):
        self.progress_label.setText(f"{stage}: {current}/{total}")
        if total > 0:
            self.progress_bar.setValue(int(100 * current / total))

    def on_sample_complete(self, sample: VEXSample):
        self.samples[sample.id] = sample
        # Update table
        self.sample_table.add_sample(sample)
        self.update_stats()

    def on_error(self, source: str, message: str):
        self.log(f"ERROR [{source}]: {message}")

    def on_finished(self, samples: list):
        self.process_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText("Complete")

        self.log(f"Processing finished: {len(samples)} samples")
        self.update_stats()
        self.statusbar.showMessage(f"Processed {len(samples)} samples")

    def on_sample_selected(self, row: int, col: int):
        # Find sample by row
        sample_id = self.sample_table.item(row, 0).text()
        # Find full sample
        for s in self.pipeline.samples.values():
            if s.id.startswith(sample_id):
                self.preview_widget.show_sample(s)
                break

    def update_stats(self):
        stats = self.pipeline.get_stats()
        self.stats_widget.update_stats(stats)

    def clear_all(self):
        self.sample_table.clear_samples()
        self.samples.clear()
        self.pipeline = IntakePipeline()
        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.progress_label.setText("Ready")
        self.update_stats()
        self.log("Cleared all data")

    def export_corpus(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Corpus",
            str(self.pipeline.output_dir / "vex_corpus.json"),
            "JSON Files (*.json)"
        )
        if file_path:
            output = self.pipeline.export_corpus(Path(file_path))
            self.log(f"Exported corpus to: {output}")
            QMessageBox.information(
                self, "Export Complete",
                f"Corpus exported to:\n{output}"
            )

    def export_training(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Training Data",
            str(self.pipeline.output_dir / "training_data.jsonl"),
            "JSONL Files (*.jsonl)"
        )
        if file_path:
            output = self.pipeline.export_training_data(Path(file_path))
            self.log(f"Exported training data to: {output}")
            QMessageBox.information(
                self, "Export Complete",
                f"Training data exported to:\n{output}"
            )

    def show_about(self):
        QMessageBox.about(
            self, "About VEX Corpus Generator",
            "VEX Corpus Generator v1.0\n\n"
            "A tool for generating VEX training corpora using Ollama.\n\n"
            "Models: Nemotron Family\n"
            "- Tier 1: nemotron-mini (4.2B)\n"
            "- Tier 2: nemotron-3-nano (31.6B)"
        )


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
