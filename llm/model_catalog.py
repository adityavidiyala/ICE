"""Gemini-only startup selection using Google's live, paginated model catalog."""
import re
from dataclasses import dataclass

from google import genai
from google.genai import types
from rich.table import Table
from rich.text import Text

from config import Config

# Google's published deprecations, reviewed 2026-11-25:
# https://aistudio.google.com/docs/deprecations
# The API catalog can still contain legacy entries; listing is not an access test.
RETIRED_IDS = {
    'gemini-pro', 'gemini-pro-vision',
    'gemini-3-pro-preview', 'gemini-3.1-flash-lite-preview',
    'gemini-2.5-pro-preview-03-25', 'gemini-2.5-pro-preview-05-06',
    'gemini-2.5-pro-preview-06-05', 'gemini-2.5-flash-preview-05-20',
    'gemini-2.5-flash-preview-09-2025', 'gemini-2.5-flash-lite-preview-09-2025',
}
LEGACY_IDS = {'gemini-2.5-pro', 'gemini-2.5-flash'}
SPECIALISED = re.compile(r'(?:^|-)(?:image|tts|audio|live|embedding|transcribe|translate|omni|robotics|computer-use)(?:-|$)')


@dataclass(frozen=True)
class ModelChoice:
    model_id: str
    display_name: str
    status: str


def model_choices(models):
    choices = {}
    for model in models:
        model_id = (model.name or '').removeprefix('models/')
        if not model_id.startswith('gemini-') or not re.fullmatch(r'[a-zA-Z0-9._-]+', model_id):
            continue
        methods = {str(action).replace('_', '').lower() for action in (model.supported_actions or [])}
        if 'generatecontent' not in methods:
            continue
        if model_id.startswith(('gemini-1.', 'gemini-2.0-')) or model_id in RETIRED_IDS:
            continue
        if SPECIALISED.search(model_id):
            continue
        description = (model.description or '').lower()
        legacy = model_id in LEGACY_IDS or 'this model is deprecated' in description
        status = 'Legacy / deprecated' if legacy else (
            'Preview / experimental' if 'preview' in model_id or '-exp' in model_id else 'Standard endpoint')
        choices[model_id] = ModelChoice(model_id, model.display_name or model_id, status)

    def order(choice):
        version = re.match(r'gemini-(\d+)(?:\.(\d+))?', choice.model_id)
        numbers = tuple(int(value or 0) for value in version.groups()) if version else (0, 0)
        return (choice.status.startswith('Legacy'), choice.status.startswith('Preview'),
                -numbers[0], -numbers[1], choice.model_id)
    return sorted(choices.values(), key=order)


def fetch_models(api_key):
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
        timeout=15000, retry_options=types.HttpRetryOptions(attempts=1)))
    try:
        # Iterating Pager follows all pages; no generation or model probes.
        return model_choices(client.models.list(config={'page_size': 100}))
    finally:
        client.close()


def select_gemini_model(console) -> bool:
    console.print(Text('Fetching Gemini models from Google…', style='dim'))
    try:
        catalog = fetch_models(Config.API_KEY)
    except (KeyboardInterrupt, EOFError):
        console.print(Text('\nModel selection cancelled.', style='dim'))
        return False
    except Exception as exc:
        # Exception URLs/messages may contain credentials. Never echo them.
        code = getattr(exc, 'code', None)
        suffix = f' (HTTP {code})' if isinstance(code, int) else ''
        console.print(Text('Could not fetch Gemini models' + suffix +
            '. Check your API key, Gemini API access, and internet connection, then restart ICE.', style='yellow'))
        return False
    if not catalog:
        console.print(Text('Google returned no eligible Gemini generateContent models. '
            'Check API access or update ICE; no guessed model will be used.', style='yellow'))
        return False

    configured = (Config.MODEL or '').removeprefix('models/')
    show_legacy = False
    console.print(Text('Choose a Gemini model by number or exact model ID.', style='#9cb9c4'))
    console.print(Text('These are API catalog entries, not a guarantee of quota or tool-calling access.\n'
        'Image/audio/Live-only and known retired endpoints are excluded.', style='dim'))
    while True:
        visible = [choice for choice in catalog if show_legacy or not choice.status.startswith('Legacy')]
        table = Table.grid(padding=(0, 2))
        for column in range(3):
            table.add_column()
        for number, choice in enumerate(visible, 1):
            label = choice.status + (' · configured' if choice.model_id == configured else '')
            table.add_row(Text(str(number), style='dim'), Text(choice.model_id), Text(label, style='dim'))
        console.print(table)
        default = next((choice for choice in visible if choice.model_id == configured), None)
        if configured and default is None:
            console.print(Text('The configured model is unavailable, specialised, retired, or hidden as legacy. '
                'Choose a listed model instead.', style='dim'))
        prompt = f'Model [Enter keeps {default.model_id}; a=include legacy; q=quit]: ' if default else \
                 'Model number or ID [a=include legacy; q=quit]: '
        try:
            answer = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            console.print(Text('\nModel selection cancelled.', style='dim'))
            return False
        if answer.lower() in {'q', 'quit', 'exit'}:
            return False
        if answer.lower() == 'a':
            show_legacy = True
            continue
        selected = None
        if not answer and default:
            selected = default
        elif answer.isdecimal() and 1 <= int(answer) <= len(visible):
            selected = visible[int(answer) - 1]
        else:
            model_id = answer.removeprefix('models/')
            selected = next((choice for choice in visible if choice.model_id == model_id), None)
        if selected is None:
            console.print(Text('Choose a number or exact ID from this list.', style='yellow'))
            continue
        if selected.status.startswith('Legacy'):
            console.print(Text('This model is deprecated or restricted to existing users. Prefer a standard endpoint. '
                'Continue anyway? [y/N]', style='yellow'))
            try:
                if input('> ').strip().lower() != 'y':
                    continue
            except (KeyboardInterrupt, EOFError):
                return False
        Config.MODEL = selected.model_id
        console.print(Text(f'Using {Config.MODEL} for this session.\n'
            'To reuse it next time, configure GEMINI_MODEL_NAME.\n', style='dim'))
        return True
