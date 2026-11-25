"""Host-only, session-scoped API-key onboarding."""
import getpass
import warnings

from rich.text import Text

from config import Config


def ensure_api_key(console) -> bool:
    """Use configured credentials or collect a hidden key before creating a chat.

    Entered keys stay in Config's process memory. Do not put them in environment
    variables, project files, tool arguments, or conversation messages.
    """
    if (Config.API_KEY or '').strip():
        return True

    console.print(Text('Welcome to ICE. Enter your Gemini API key to get started.', style='cyan'))
    console.print(Text(
        'The key stays in memory for this session. To skip this prompt next time,\n'
        'configure GEMINI_API_KEY in your environment or ICE\'s existing .env file.\n'
        'Press Ctrl+C to cancel.', style='dim'))

    while True:
        try:
            # getpass can otherwise fall back to visible input in unsupported
            # terminals. Refuse that fallback rather than exposing the key.
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                key = getpass.getpass('Gemini API key (hidden): ').strip()
        except (KeyboardInterrupt, EOFError):
            console.print(Text('\nAPI-key setup cancelled.', style='dim'))
            return False
        except getpass.GetPassWarning:
            console.print(Text(
                'Hidden input is unavailable in this terminal. Configure GEMINI_API_KEY and restart ICE.',
                style='yellow'))
            return False
        if not key:
            console.print(Text('Please enter a non-empty API key, or press Ctrl+C to cancel.', style='dim'))
            continue
        Config.API_KEY = key
        console.print(Text('API key set for this session.\n', style='dim'))
        return True
