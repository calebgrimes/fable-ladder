#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install.sh --dry-run|--copy [--target DIR]

Install the orchestrate skill under DIR/orchestrate. DIR defaults to ~/.claude/skills.
USAGE
}

mode=''
target_root="${ORCH_SKILLS_DIR:-}"

while (($#)); do
  case "$1" in
    --dry-run|--copy)
      [[ -z "$mode" ]] || { echo 'Choose only one of --dry-run or --copy.' >&2; exit 64; }
      mode="${1#--}"
      ;;
    --target)
      (($# >= 2)) || { echo '--target requires a directory.' >&2; exit 64; }
      target_root="$2"
      shift
      ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 64 ;;
  esac
  shift
done

[[ -n "$mode" ]] || { usage >&2; exit 64; }
if [[ -z "$target_root" ]]; then
  [[ -n "${HOME:-}" ]] || { echo 'Set HOME or pass --target DIR.' >&2; exit 64; }
  target_root="$HOME/.claude/skills"
fi
[[ "$target_root" != '/' ]] || { echo 'Refusing to install directly under /.' >&2; exit 64; }

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source_root="$repo_root/skill/orchestrate"
destination="$target_root/orchestrate"
files=(SKILL.md scripts/ask_opus.sh templates/brief.md templates/board.md)

for relative_path in "${files[@]}"; do
  [[ -f "$source_root/$relative_path" ]] || { echo "Missing repository file: $source_root/$relative_path" >&2; exit 66; }
done

if [[ "$mode" == 'dry-run' ]]; then
  printf 'Would create directory: %s\n' "$destination/scripts" "$destination/templates"
  for relative_path in "${files[@]}"; do printf 'Would copy: %s\n' "$destination/$relative_path"; done
  printf 'Would set executable mode: %s\n' "$destination/scripts/ask_opus.sh"
  exit 0
fi

if [[ -e "$destination" && ! -d "$destination" ]]; then
  echo "Destination exists and is not a directory: $destination" >&2
  exit 73
fi

mkdir -p "$destination/scripts" "$destination/templates"
for relative_path in "${files[@]}"; do cp "$source_root/$relative_path" "$destination/$relative_path"; done
chmod 0755 "$destination/scripts/ask_opus.sh"
printf 'Installed orchestrate skill at %s\n' "$destination"
