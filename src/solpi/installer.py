"""One-Command Multi-Harness Installer (`solpi install`).

Installs the SoL-Pi Skill, Rules, Lifecycle Hooks and MCP server across:
- `agy`:      Google Antigravity (`~/.gemini/config/` or workspace `.agents/`)
- `claude`:   Claude Code (skill + `.mcp.json`/`~/.claude.json` + hooks in `settings(.local).json`)
- `codex`:    OpenAI Codex CLI (`config.toml` MCP table, `hooks.json`, `.agents/skills`, `AGENTS.md`)
- `opencode`: OpenCode (`opencode.json` MCP entry, `.opencode/skills`, `AGENTS.md`; no hook API)
- `cursor`:   Cursor IDE (`mcp.json` + an always-applied `.mdc` rule)
- `all`:      Everything above

Safety rules for every file we do not own:
- A file that exists but cannot be parsed is NEVER overwritten (the target is reported as an error).
- The first modification of an existing file keeps a `<name>.solpi.bak` copy.
- Writes are atomic (temp file + rename) and idempotent (unchanged content is not rewritten).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import solpi

HOOK_MARKER = "solpi.hook_runner"
MD_BEGIN = "<!-- >>> sol-pi (managed by `solpi install`) >>> -->"
MD_END = "<!-- <<< sol-pi <<< -->"
TOML_BEGIN = "# >>> sol-pi (managed by `solpi install`) >>>"
TOML_END = "# <<< sol-pi <<<"
ALL_TARGETS = ["agy", "claude", "codex", "opencode", "cursor"]


class InstallError(RuntimeError):
    """A target could not be installed safely."""


# --------------------------------------------------------------------------- paths


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def get_src_dir() -> Path:
    """Directory that must be on PYTHONPATH to `import solpi` (repo `src/` or site-packages)."""
    return Path(solpi.__file__).resolve().parent.parent


# --------------------------------------------------------------------------- safe file io


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if path.exists():
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _write_if_changed(path: Path, new_text: str, dry_run: bool) -> bool:
    """Write `new_text`; keep a one-time `.solpi.bak`. Returns True if the file changed."""
    old_text = path.read_text(encoding="utf-8") if path.is_file() else None
    if old_text == new_text:
        return False
    if not dry_run:
        if old_text is not None:
            backup = path.with_name(path.name + ".solpi.bak")
            if not backup.exists():
                shutil.copy2(path, backup)
        _atomic_write(path, new_text)
    return True


def read_json_strict(path: Path) -> Dict[str, Any]:
    """Load a JSON object. Missing/empty -> {}; anything unparsable raises (never clobber user data)."""
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise InstallError(
            f"{path} is not valid JSON ({err.msg} at line {err.lineno}); refusing to overwrite it. "
            f"Fix or move the file and re-run."
        ) from err
    if not isinstance(data, dict):
        raise InstallError(f"{path} does not contain a JSON object; refusing to overwrite it.")
    return data


def _save_json(path: Path, data: Dict[str, Any], dry_run: bool) -> bool:
    return _write_if_changed(path, json.dumps(data, indent=2) + "\n", dry_run)


def _upsert_block(text: str, block: str, begin: str, end: str) -> str:
    """Insert or replace a managed block delimited by `begin`/`end` markers."""
    full = f"{begin}\n{block.strip()}\n{end}\n"
    if begin in text and end in text:
        head, rest = text.split(begin, 1)
        _, tail = rest.split(end, 1)
        return head + full + tail.lstrip("\n") if tail.strip() else head + full
    sep = "" if not text else ("\n" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n"))
    return text + sep + full


def _status(changed: bool, what: str, path: Path) -> str:
    return f"{what} {'->' if changed else 'already up to date:'} {path}"


# --------------------------------------------------------------------------- command builders


def _env_prefix(src_dir: Path) -> str:
    return f"env PYTHONPATH={shlex.quote(str(src_dir))}"


def solpi_cli_command(src_dir: Path, python_exe: str) -> str:
    return f"{_env_prefix(src_dir)} {shlex.quote(python_exe)} -m solpi.cli"


def hook_command(event: str, src_dir: Path, python_exe: str, hook_mode: Optional[str] = None) -> str:
    cli = solpi_cli_command(src_dir, python_exe)
    mode_env = f" SOLPI_HOOK_MODE={shlex.quote(hook_mode)}" if hook_mode else ""
    return (
        f"{_env_prefix(src_dir)}{mode_env} {shlex.quote(python_exe)} -m {HOOK_MARKER} "
        f"{event} {shlex.quote(cli)}"
    )


def get_mcp_entry(repo_root: Optional[Path] = None, hook_only: bool = False) -> Dict[str, Any]:
    """Portable MCP stdio entry (`command`/`args`/`env`) pointing to `solpi.mcp_server`.

    With `hook_only` the server advertises just `solpi_read_obs` (~140 tokens per request instead of ~735).
    """
    env = {"PYTHONPATH": str(get_src_dir())}
    if hook_only:
        env["SOLPI_MCP_TOOLS"] = "read"
    return {"command": sys.executable or "python3", "args": ["-m", "solpi.mcp_server"], "env": env}


def build_hooks_json(
    repo_root: Optional[Path] = None, hook_only: bool = False, hook_mode: Optional[str] = None
) -> Dict[str, Any]:
    """Antigravity (AGY) hooks.json block."""
    src, py = get_src_dir(), sys.executable or "python3"
    block: Dict[str, Any] = {
        "enabled": True,
        "PreToolUse": [
            {
                "matcher": "run_command|Bash|bash",
                "hooks": [{"type": "command", "command": hook_command("PreToolUse", src, py, hook_mode), "timeout": 10}],
            }
        ],
    }
    if not hook_only:  # the compaction notice points at `solpi_milestone`, which hook-only does not expose
        block["PreInvocation"] = [
            {"type": "command", "command": hook_command("PreInvocation", src, py), "timeout": 5}
        ]
    return {"sol-pi-observation-pack": block}


def build_claude_style_hooks(
    bash_matcher: str, hook_only: bool = False, hook_mode: Optional[str] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """`hooks` section shared by Claude Code (settings.json) and Codex (hooks.json)."""
    src, py = get_src_dir(), sys.executable or "python3"
    hooks: Dict[str, List[Dict[str, Any]]] = {
        "PreToolUse": [
            {
                "matcher": bash_matcher,
                "hooks": [{"type": "command", "command": hook_command("PreToolUse", src, py, hook_mode), "timeout": 10}],
            }
        ]
    }
    if not hook_only:
        hooks["UserPromptSubmit"] = [
            {"hooks": [{"type": "command", "command": hook_command("UserPromptSubmit", src, py), "timeout": 5}]}
        ]
    return hooks


def _is_solpi_group(group: Any) -> bool:
    return isinstance(group, dict) and any(
        HOOK_MARKER in str(h.get("command", "")) for h in (group.get("hooks") or []) if isinstance(h, dict)
    )


def merge_claude_style_hooks(
    path: Path, bash_matcher: str, dry_run: bool, hook_only: bool = False, hook_mode: Optional[str] = None
) -> bool:
    data = read_json_strict(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError(f"{path}: `hooks` is not an object; refusing to overwrite it.")
    wanted = build_claude_style_hooks(bash_matcher, hook_only, hook_mode)
    # events we own but no longer want (hook-only drops UserPromptSubmit) lose our stale group
    for event in ("PreToolUse", "UserPromptSubmit"):
        if event not in wanted and isinstance(hooks.get(event), list):
            hooks[event] = [g for g in hooks[event] if not _is_solpi_group(g)]
            if not hooks[event]:
                del hooks[event]
    for event, groups in wanted.items():
        existing = hooks.get(event, [])
        if not isinstance(existing, list):
            raise InstallError(f"{path}: `hooks.{event}` is not a list; refusing to overwrite it.")
        hooks[event] = [g for g in existing if not _is_solpi_group(g)] + groups
    return _save_json(path, data, dry_run)


# --------------------------------------------------------------------------- MCP writers


def merge_mcp_json(target_mcp_file: Path, server_name: str, entry: Dict[str, Any], dry_run: bool = False) -> str:
    data = read_json_strict(target_mcp_file)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise InstallError(f"{target_mcp_file}: `mcpServers` is not an object; refusing to overwrite it.")
    servers[server_name] = entry
    changed = _save_json(target_mcp_file, data, dry_run)
    return _status(changed, f"Registered MCP server `{server_name}`", target_mcp_file)


def merge_codex_toml(config_file: Path, server_name: str, entry: Dict[str, Any], dry_run: bool = False) -> str:
    """Add `[mcp_servers.<name>]` to Codex `config.toml` as a managed block (no TOML library needed)."""
    text = config_file.read_text(encoding="utf-8") if config_file.is_file() else ""
    outside = text
    if TOML_BEGIN in text and TOML_END in text:
        head, rest = text.split(TOML_BEGIN, 1)
        outside = head + rest.split(TOML_END, 1)[1]
    if re.search(rf"^\s*\[mcp_servers\.[\"']?{re.escape(server_name)}[\"']?(\.[^\]]*)?\]", outside, re.M):
        raise InstallError(
            f"{config_file} already defines [mcp_servers.{server_name}] outside the managed block; "
            f"remove it or rename it, then re-run."
        )
    q = json.dumps
    lines = [
        f"[mcp_servers.{server_name}]",
        f"command = {q(entry['command'])}",
        f"args = [{', '.join(q(a) for a in entry['args'])}]",
        "",
        f"[mcp_servers.{server_name}.env]",
        *[f"{k} = {q(v)}" for k, v in entry["env"].items()],
    ]
    new_text = _upsert_block(text, "\n".join(lines), TOML_BEGIN, TOML_END)
    changed = _write_if_changed(config_file, new_text, dry_run)
    return _status(changed, f"Registered Codex MCP server `{server_name}`", config_file)


def detect_opencode_schema() -> Optional[str]:
    exe = shutil.which("opencode")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.\d+", out)
    if not m:
        return None
    return "v2" if int(m.group(1)) >= 2 else "v1"


def merge_opencode_json(
    config_file: Path, server_name: str, entry: Dict[str, Any], schema: str = "auto", dry_run: bool = False
) -> str:
    """OpenCode V1 uses `mcp.<name>`; V2 uses `mcp.servers.<name>` (and `disabled` instead of `enabled`)."""
    data = read_json_strict(config_file)
    mcp = data.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        raise InstallError(f"{config_file}: `mcp` is not an object; refusing to overwrite it.")
    if schema == "auto":
        if isinstance(mcp.get("servers"), dict):
            schema = "v2"
        elif mcp:
            schema = "v1"
        else:
            schema = detect_opencode_schema() or "v2"
    oc_entry: Dict[str, Any] = {
        "type": "local",
        "command": [entry["command"], *entry["args"]],
        "environment": dict(entry["env"]),
    }
    if schema == "v1":
        oc_entry["enabled"] = True
        mcp[server_name] = oc_entry
    else:
        mcp.setdefault("servers", {})[server_name] = oc_entry
    changed = _save_json(config_file, data, dry_run)
    return _status(changed, f"Registered OpenCode ({schema}) MCP server `{server_name}`", config_file)


# --------------------------------------------------------------------------- skill & rules


def copy_skill_bundle(dest_skills_dir: Path, repo_root: Optional[Path] = None, dry_run: bool = False) -> str:
    repo_root = repo_root or get_repo_root()
    src_skill = repo_root / "skills" / "sol-pi-efficiency"
    src_pkg = Path(solpi.__file__).resolve().parent
    if not (src_skill / "SKILL.md").is_file():
        raise InstallError(
            f"Skill sources not found at {src_skill}. Run `solpi install` from a source checkout "
            f"(`pip install -e .`), not from a built wheel."
        )
    dest_skill = dest_skills_dir / "sol-pi-efficiency"
    if not dry_run:
        dest_skills_dir.mkdir(parents=True, exist_ok=True)
        if dest_skill.exists():
            shutil.rmtree(dest_skill)
        shutil.copytree(src_skill, dest_skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # Bundle the engine so the copied skill works standalone without pip install
        vendor_solpi = dest_skill / "scripts" / "vendor" / "solpi"
        vendor_solpi.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_pkg, vendor_solpi, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return f"Installed skill `sol-pi-efficiency` (with bundled engine) -> {dest_skill}"


def _rules_text(repo_root: Optional[Path] = None) -> str:
    src = (repo_root or get_repo_root()) / "rules" / "AGENTS.md"
    if not src.is_file():
        raise InstallError(f"Rules source not found at {src}.")
    return src.read_text(encoding="utf-8")


def install_md_rules(dest: Path, dry_run: bool) -> str:
    """Append/refresh the SoL-Pi protocol as a managed block inside an AGENTS.md."""
    text = dest.read_text(encoding="utf-8") if dest.is_file() else ""
    changed = _write_if_changed(dest, _upsert_block(text, _rules_text(), MD_BEGIN, MD_END), dry_run)
    return _status(changed, "SoL-Pi rules block", dest)


# --------------------------------------------------------------------------- per-harness installers


def _install_agy(base: Path, dry_run: bool, hook_only: bool = False, hook_mode: Optional[str] = None, **_: Any) -> List[str]:
    out: List[str] = []
    if not hook_only:
        out.append(copy_skill_bundle(base / "skills", dry_run=dry_run))
    out.append(merge_mcp_json(base / "mcp_config.json", "sol-pi", get_mcp_entry(hook_only=hook_only), dry_run=dry_run))
    hooks_file = base / "hooks.json"
    hooks_data = read_json_strict(hooks_file)
    hooks_data.pop("sol-pi-observation-pack", None)
    hooks_data.update(build_hooks_json(hook_only=hook_only, hook_mode=hook_mode))
    out.append(_status(_save_json(hooks_file, hooks_data, dry_run), "Merged AGY lifecycle hooks", hooks_file))
    if not hook_only:
        rules_file = base / "rules" / "sol-pi.md"
        out.append(_status(_write_if_changed(rules_file, _rules_text(), dry_run), "SoL-Pi rule file", rules_file))
    return out


def _install_claude(ws: Path, home: Path, scope: str, dry_run: bool, hook_only: bool = False,
                    hook_mode: Optional[str] = None, **_: Any) -> List[str]:
    glob = scope == "global"
    base = (home / ".claude") if glob else (ws / ".claude")
    mcp_file = (home / ".claude.json") if glob else (ws / ".mcp.json")
    # Hooks embed absolute local paths, so a workspace install goes to the *local* (uncommitted) file.
    settings = base / ("settings.json" if glob else "settings.local.json")
    out: List[str] = []
    if not hook_only:
        out.append(copy_skill_bundle(base / "skills", dry_run=dry_run))
    out += [
        merge_mcp_json(mcp_file, "sol-pi", get_mcp_entry(hook_only=hook_only), dry_run=dry_run),
        _status(merge_claude_style_hooks(settings, "Bash", dry_run, hook_only, hook_mode), "Merged Claude Code hooks", settings),
    ]
    if not glob:
        out.append("Note: Claude Code asks you to approve project-scoped `.mcp.json` servers on first use.")
    return out


def _install_codex(ws: Path, home: Path, scope: str, dry_run: bool, hook_only: bool = False,
                   hook_mode: Optional[str] = None, **_: Any) -> List[str]:
    glob = scope == "global"
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex")) if glob else (ws / ".codex")
    skills = (home / ".agents" / "skills") if glob else (ws / ".agents" / "skills")
    agents_md = (codex_home / "AGENTS.md") if glob else (ws / "AGENTS.md")
    hooks_file = codex_home / "hooks.json"
    out: List[str] = []
    if not hook_only:
        out.append(copy_skill_bundle(skills, dry_run=dry_run))
    out += [
        merge_codex_toml(codex_home / "config.toml", "sol-pi", get_mcp_entry(hook_only=hook_only), dry_run=dry_run),
        _status(merge_claude_style_hooks(hooks_file, "^Bash$", dry_run, hook_only, hook_mode), "Merged Codex hooks", hooks_file),
    ]
    if not hook_only:
        out.append(install_md_rules(agents_md, dry_run))
    if not glob:
        out.append("Note: Codex loads project `.codex/` config and hooks only for trusted projects.")
    return out


def _install_opencode(ws: Path, home: Path, scope: str, dry_run: bool, opencode_schema: str = "auto",
                      hook_only: bool = False, **_: Any) -> List[str]:
    if hook_only:
        return ["[SKIP] opencode: it has no shell-hook API, so a hook-only install would have nothing to do "
                "(use the full install: the agent then calls `solpi_run` itself)."]
    glob = scope == "global"
    cfg_dir = (home / ".config" / "opencode") if glob else ws
    cfg_file = cfg_dir / "opencode.json"
    if not cfg_file.exists() and (cfg_dir / "opencode.jsonc").is_file():
        cfg_file = cfg_dir / "opencode.jsonc"
    skills = (cfg_dir / "skills") if glob else (ws / ".opencode" / "skills")
    agents_md = (cfg_dir / "AGENTS.md") if glob else (ws / "AGENTS.md")
    return [
        copy_skill_bundle(skills, dry_run=dry_run),
        merge_opencode_json(cfg_file, "sol-pi", get_mcp_entry(), schema=opencode_schema, dry_run=dry_run),
        install_md_rules(agents_md, dry_run),
        "Note: OpenCode has no shell-hook API; SoL-Pi is used through the MCP tools + AGENTS.md rules "
        "(no transparent command rewriting).",
    ]


def _install_cursor(ws: Path, home: Path, scope: str, dry_run: bool, hook_only: bool = False, **_: Any) -> List[str]:
    if hook_only:
        return ["[SKIP] cursor: it has no shell-hook API, so a hook-only install would have nothing to do."]
    base = (home / ".cursor") if scope == "global" else (ws / ".cursor")
    rules_dest = base / "rules" / "sol-pi.mdc"
    mdc = (
        "---\ndescription: SoL-Pi agent efficiency protocol\nalwaysApply: true\n---\n\n" + _rules_text()
    )
    return [
        merge_mcp_json(base / "mcp.json", "sol-pi", get_mcp_entry(), dry_run=dry_run),
        _status(_write_if_changed(rules_dest, mdc, dry_run), "Cursor rule", rules_dest),
    ]


def install_for_target(
    target: str,
    *,
    scope: str = "workspace",
    workspace_dir: Optional[Path] = None,
    dry_run: bool = False,
    opencode_schema: str = "auto",
    hook_only: bool = False,
    hook_mode: Optional[str] = None,
) -> List[str]:
    """Install SoL-Pi for one or all harnesses. Failures are reported as `[ERROR] ...` entries.

    `hook_only`: just the PreToolUse hook + an MCP server exposing only `solpi_read_obs` (~140 tokens of
    fixed context per request instead of ~735); no skill, no rules, no compaction notice.
    `hook_mode`: `safe` or `prefixed` (see `hook_runner.should_wrap_command`); stored in the hook command.
    """
    if hook_mode not in (None, "safe", "prefixed"):
        return [f"[ERROR] invalid hook mode {hook_mode!r} (use safe or prefixed)"]
    ws = (workspace_dir or Path.cwd()).expanduser().resolve()
    home = Path.home()
    targets = ALL_TARGETS if target == "all" else [target]
    actions: List[str] = []
    kw = dict(hook_only=hook_only, hook_mode=hook_mode)

    for tgt in targets:
        try:
            if tgt == "agy":
                base = (home / ".gemini" / "config") if scope == "global" else (ws / ".agents")
                actions += _install_agy(base, dry_run, **kw)
            elif tgt == "claude":
                actions += _install_claude(ws, home, scope, dry_run, **kw)
            elif tgt == "codex":
                actions += _install_codex(ws, home, scope, dry_run, **kw)
            elif tgt == "opencode":
                actions += _install_opencode(ws, home, scope, dry_run, opencode_schema=opencode_schema, hook_only=hook_only)
            elif tgt == "cursor":
                actions += _install_cursor(ws, home, scope, dry_run, hook_only=hook_only)
            else:
                actions.append(f"[ERROR] Unknown harness target: {tgt}")
        except (InstallError, OSError) as err:
            actions.append(f"[ERROR] {tgt}: {err}")
    return actions
