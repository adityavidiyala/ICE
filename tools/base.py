def _ask_approval(action_desc: str) -> bool:
    """Helper to prompt the user for (y/n) approval before dangerous actions."""
    print(f"\n[⚠️ AGENT REQUEST] {action_desc}")
    choice = input("Approve? (y/n): ").strip().lower()
    return choice == 'y'