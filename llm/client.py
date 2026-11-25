from google import genai
from google.genai import types
from config import Config
from context.manager import ContextManager
import json

class LLMClient:
    def __init__(self, sandboxed=False):
        self.client = genai.Client(api_key=Config.API_KEY)
        self.context = ContextManager()

        instructions = (
            "You are ICE (Interactive Coding Environment), an autonomous AI software engineer operating directly in the user's terminal. "
            "Your goal is to complete coding tasks, fix bugs, and execute commands autonomously.\n\n"

            "ENVIRONMENT & EXECUTION RULES:\n"
            "1. NO INTERACTIVE PROMPTS: You operate in a headless, non-interactive shell. NEVER write scripts or run commands that wait for user input (e.g., Python's `input()`, bash `read`, or pausing for confirmation). "
            "Always use command-line arguments (sys.argv), environment variables, or hardcoded test values.\n"
            "2. SELF-CORRECTION: If a command times out, assume it is waiting for input or hanging, and rewrite it to be non-interactive.\n\n"

            "FORMATTING & DISPLAY RULES:\n"
            "1. TERMINAL-SAFE TEXT: Your output is rendered in a basic terminal. NEVER use LaTeX, MathJax, or math-specific formatting (like $1^3$, \\rightarrow, or \\frac). Use plain text ASCII equivalents (e.g., ->, =).\n"
            "2. CONCISENESS: Terminal screen real estate is limited. Do not write long, overly verbose explanations. State what you are doing, execute the tools, and provide a brief summary of the result.\n\n"

            "TOOL USAGE:\n"
            "1. Autonomously use your provided tools (read_file, write_file, edit_file, run_command, grep, glob, list_directories, plan, install_dependencies) to explore the codebase, implement solutions, and verify they work.\n"
            "2. Verify code changes with a relevant non-interactive check via `run_command` before declaring a coding task complete. Do not run commands for greetings or conversational answers.\n"
            "3. Use tools only to resolve a concrete uncertainty or perform required work. Answer greetings and general questions directly without exploring the workspace.\n"
            "4. Reuse observations already in the conversation. Inspect an unfamiliar workspace once when needed. Do not repeat a directory listing or equivalent shell listing unless relevant files have changed, the earlier result was incomplete, or the user requests a fresh check.\n"
            "5. When a listing reports no visible files or directories, proceed to create the requested project. Do not probe guessed project subdirectories or use glob/grep to reconfirm the same empty workspace. A missing directory result is sufficient; create it through write_file when needed. Hidden and excluded entries may still exist, so do not assume the directory is completely empty.\n"
            "6. For a straightforward task, such as creating a small HTML/CSS/JavaScript game, use a brief approach in your response and begin implementation. Reserve the plan tool for complex work that benefits from progress tracking; do not create separate plan steps for routine listing, designing, or each individual file.\n"
            "7. When using plan, group work into a few meaningful milestones. Complete a step only after performing its work. Use the exact step IDs returned by the tool; never invent IDs such as '1' or '2'. Reuse IDs from previous results instead of calling plan list when they are already available."
            "\n8. Create and modify project files using write_file and edit_file. Do not use node -e, python -c, PowerShell scripts, or shell redirection as substitutes for file editing. Reserve run_command for execution, checks, and tasks the dedicated tools cannot perform. Prefer node --check <file> for JavaScript syntax checks when that is the relevant verification. Never change the tool choice or obfuscate a command to bypass an approval denial."
        )

        if sandboxed:
            instructions += (
                "\n\nSANDBOX ENVIRONMENT:\n"
                "All file and command tools operate in a Linux Docker working copy at /workspace. "
                "Use project-relative POSIX paths and non-interactive bash commands, not Windows PowerShell. "
                "Python, Node, npm and Git are installed; project dependencies are not automatically installed. "
                "Ordinary commands run offline. To install missing dependencies, use install_dependencies after creating "
                "or updating package.json or requirements.txt; it asks the user for temporary general internet access "
                "and turns networking off again afterward. Never retry a denied install or bypass this workflow. "
                "Use manager npm or pip explicitly when both manifests exist. Install scripts/builds are disabled by "
                "default; request allow_scripts=True only when a package requires them, explaining why. "
                "Do not reinstall after success unless requirements changed or verification identifies a missing package. "
                "Python's /workspace/.venv and project node_modules are used automatically by subsequent commands. "
                "Host files, credentials, .git metadata and common generated directories are not copied. "
                "Changes remain staged until the user runs /apply. Report completion as changes in the sandbox, "
                "not as host files already updated. Never try to apply host changes yourself. "
                "Finish with /apply guidance and brief Windows setup/start commands based on the project's manifests. "
                "Explain that Linux dependencies stay in Docker; Windows needs its own installation once and when requirements change."
            )

        self.generation_config = types.GenerateContentConfig(
            system_instruction=instructions,
            temperature=0.0,
            # Agent.run executes tools and emits their UI events. The SDK must
            # return tool calls without executing the Python functions itself.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        self.chat_session = self.client.chats.create(
            model=Config.MODEL,
            config=self.generation_config,
        )

    def send(self, message, tools: list = None) -> tuple:
        """Send a message to the model.

        Returns:
            (response, token_count) — token_count is 0 if usage metadata
            is not available (e.g. on tool-result turns).
        """
        if tools:
            self.generation_config.tools = tools

        response = self.chat_session.send_message(
            message,
            config=self.generation_config,
        )

        return self._finish_response(response)

    def send_stream(self, message, tools: list = None):
        """Yield visible text immediately; return the complete SDK response.

        The stream must finish before tools execute, preserving full function
        arguments and thought signatures in the SDK's conversation history.
        """
        from agent.events import AgentEvent

        if tools:
            self.generation_config.tools = tools
        parts = []
        usage = None
        finish_reason = None
        stream = self.chat_session.send_message_stream(message, config=self.generation_config)
        try:
            for chunk in stream:
                if chunk.usage_metadata is not None:
                    usage = chunk.usage_metadata
                if not chunk.candidates:
                    continue
                candidate = chunk.candidates[0]
                finish_reason = candidate.finish_reason or finish_reason
                if candidate.content:
                    for part in candidate.content.parts or []:
                        parts.append(part)
                        if part.text and not part.thought:
                            yield AgentEvent.text_delta(part.text)
        finally:
            stream.close()
        if not parts:
            raise RuntimeError("The model returned an empty response. Please try again.")
        response = types.GenerateContentResponse(
            candidates=[types.Candidate(
                content=types.Content(role="model", parts=parts),
                finish_reason=finish_reason,
            )], usage_metadata=usage,
        )
        return self._finish_response(response)

    def _finish_response(self, response):
        # Extract token usage only after the complete response is available.
        token_count = 0
        if response.usage_metadata and response.usage_metadata.total_token_count:
            token_count = response.usage_metadata.total_token_count
            self.context.record_token_usage(token_count)

        # ── Auto-prune if threshold exceeded ──────────────────────────────────
        if self.context.needs_pruning():
            self._run_pruning()

        return response, token_count

    def _run_pruning(self) -> None:
        """Prune stale tool outputs and rebuild the chat session."""
        raw_history = self.chat_session.get_history(curated=True)
        pruned_history, n_pruned = self.context.prune_history(raw_history)

        if n_pruned == 0:
            return

        # Rebuild the session with the pruned history
        self.chat_session = self.client.chats.create(
            model=Config.MODEL,
            config=self.generation_config,
            history=pruned_history,
        )

    def switch_model(self, model_id):
        """Retain conversation/tool history without replaying model-specific signatures."""
        history = []
        for content in self.chat_session.get_history(curated=True):
            parts = []
            for part in content.parts or []:
                if part.thought:
                    continue
                if part.function_call:
                    call = part.function_call
                    parts.append(types.Part.from_text(text=f'[Previous tool call: {call.name}] ' +
                        json.dumps(dict(call.args or {}), ensure_ascii=False, default=str)))
                elif part.function_response:
                    result = part.function_response
                    parts.append(types.Part.from_text(text=f'[Previous tool result: {result.name}] ' +
                        json.dumps(result.response, ensure_ascii=False, default=str)))
                else:
                    parts.append(part.model_copy(update={'thought_signature': None}))
            if parts:
                history.append(types.Content(role=content.role, parts=parts))
        session = self.client.chats.create(model=model_id, config=self.generation_config, history=history)
        self.chat_session = session
        Config.MODEL = model_id

