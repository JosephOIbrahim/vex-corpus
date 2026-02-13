"""
Houdini hrpyc Server Script

Run this script inside Houdini's Python Shell to enable the RPC server
for VEX Corpus integration.

Houdini 21+ uses hrpyc (Houdini Remote Python Connection) instead of
the old commandport system.

Usage in Houdini:
    1. Open Windows > Python Shell
    2. Paste and run this script

Or add to your 123.py or 456.py for auto-start.
"""

import hou

def start_vex_corpus_server(port=18811):
    """
    Start the hrpyc server for VEX Corpus integration.

    Args:
        port: The port number (default 18811)
    """
    try:
        import hrpyc

        # Start the RPC server
        hrpyc.start_server(port=port)

        print(f"[VEX CORPUS] hrpyc server started on port {port}")
        print(f"[VEX CORPUS] VEX Corpus can now connect and pull VEX code")
        print(f"[VEX CORPUS] Run: python vex.py --houdini --houdini-port {port}")
        return True

    except Exception as e:
        print(f"[VEX CORPUS] ERROR: Could not start server: {e}")
        return False


def stop_vex_corpus_server():
    """Stop the hrpyc server."""
    try:
        import hrpyc
        hrpyc.stop_server()
        print("[VEX CORPUS] Server stopped")
    except Exception as e:
        print(f"[VEX CORPUS] Could not stop server: {e}")


def list_wrangles():
    """List all wrangle nodes in the current scene."""
    wrangles = []
    wrangle_types = ["attribwrangle", "volumewrangle", "popwrangle", "gaswrangle"]

    for node in hou.node("/").allSubChildren():
        try:
            type_name = node.type().name()
            if any(wt in type_name for wt in wrangle_types):
                snippet = node.parm("snippet")
                if snippet:
                    code = snippet.evalAsString()
                    if code and code.strip():
                        wrangles.append({
                            "path": node.path(),
                            "type": type_name,
                            "lines": len(code.split('\n'))
                        })
        except:
            pass

    print(f"\n[VEX CORPUS] Found {len(wrangles)} wrangle nodes:")
    for w in wrangles:
        print(f"  {w['path']} ({w['type']}, {w['lines']} lines)")

    return wrangles


# Auto-run when pasted into Python Shell
if __name__ == "__main__" or hou.isUIAvailable():
    print("\n" + "="*50)
    print("  VEX CORPUS - Houdini Integration")
    print("="*50)
    print("\nAvailable functions:")
    print("  start_vex_corpus_server(18811)  - Start RPC server")
    print("  stop_vex_corpus_server()        - Stop RPC server")
    print("  list_wrangles()                 - List all wrangles")
    print("\nStarting server on port 18811...")
    start_vex_corpus_server(18811)
