from .console import console
from .display import render_banner, render_event

def run_repl():
    # Tools can import presentation hooks without importing the agent loop.
    from .prompt import run_repl as start
    return start()
