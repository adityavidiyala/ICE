def install_dependencies(manager: str = 'auto', path: str = '.', timeout: int = 300,
                         allow_scripts: bool = False) -> str:
    """Install dependencies from an existing project manifest in the Docker sandbox.

    Always asks approval before temporarily enabling general internet access.
    Ordinary tools pause during installation; networking is disabled afterward.
    Dependencies stay in Docker and are not copied to Windows by /apply.
    Write/update the project's dependency manifest first. Use this tool instead
    of repeatedly retrying npm/pip installs through the offline run_command tool.

    Args:
        manager: 'auto', 'npm', or 'pip'. Auto detects the project manifest.
        path: Project directory relative to /workspace (default '.').
        timeout: Installation timeout in seconds, maximum 900.
        allow_scripts: Allow package install/build scripts after explicit approval.
            Defaults to False: npm install scripts are skipped and pip uses wheels only.
    """
    return 'Error: Dependency installation requires the Docker sandbox backend. No host installation attempted.'
