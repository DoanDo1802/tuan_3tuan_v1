#!/usr/bin/env bash
# Đẩy code lên server qua rsync + SSH.
# Sử dụng:
#   ./deploy.sh                     # dry-run (xem trước file nào sẽ thay đổi)
#   ./deploy.sh --apply             # thực sự đồng bộ
#   ./deploy.sh --apply --delete    # đồng bộ + XÓA file ở server đã bị xóa local
#
# CẤU HÌNH: sửa các biến SERVER_* dưới đây, hoặc đặt trong file deploy.sh.local
# (file này được .rsync-exclude bỏ qua nên an toàn để giữ thông tin server).

set -euo pipefail

# ---- Cấu hình mặc định (chỉnh tại đây) -----------------------------------
SERVER_USER="${SERVER_USER:-doando}"
SERVER_HOST="${SERVER_HOST:-CHANGE_ME_SERVER_IP_OR_HOSTNAME}"
SERVER_PATH="${SERVER_PATH:-/home/${SERVER_USER}/ai_recognition_project}"
SSH_PORT="${SSH_PORT:-22}"

# Nạp override từ deploy.sh.local nếu có (KHÔNG commit file đó lên git).
if [[ -f "$(dirname "$0")/deploy.sh.local" ]]; then
  # shellcheck disable=SC1091
  source "$(dirname "$0")/deploy.sh.local"
fi

# ---- Parse args ----------------------------------------------------------
APPLY=0
DELETE=0
for arg in "$@"; do
  case "$arg" in
    --apply)  APPLY=1 ;;
    --delete) DELETE=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# ---- Sanity checks -------------------------------------------------------
if [[ "$SERVER_HOST" == "CHANGE_ME_"* ]]; then
  echo "ERROR: chưa cấu hình SERVER_HOST. Sửa deploy.sh hoặc tạo deploy.sh.local." >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f .rsync-exclude ]]; then
  echo "ERROR: không tìm thấy .rsync-exclude tại $SCRIPT_DIR" >&2
  exit 2
fi

# ---- SSH ControlMaster: chỉ nhập password 1 lần cho cả mkdir + rsync ----
mkdir -p ~/.ssh/ctl 2>/dev/null || true
SSH_CTL_PATH="$HOME/.ssh/ctl/deploy-%h-%p-%r"
SSH_BASE="ssh -p ${SSH_PORT} -o ControlMaster=auto -o ControlPath=${SSH_CTL_PATH} -o ControlPersist=120s"

cleanup_ssh() {
  $SSH_BASE -O exit "${SERVER_USER}@${SERVER_HOST}" 2>/dev/null || true
}
trap cleanup_ssh EXIT

# ---- Build rsync command -------------------------------------------------
RSYNC_OPTS=(
  -avh
  --exclude-from=.rsync-exclude
  -e "$SSH_BASE"
)
if [[ "$DELETE" -eq 1 ]]; then
  RSYNC_OPTS+=(--delete)
fi
if [[ "$APPLY" -eq 0 ]]; then
  RSYNC_OPTS+=(--dry-run)
  echo ">>> DRY-RUN: thêm --apply để thực sự đẩy file."
fi

TARGET="${SERVER_USER}@${SERVER_HOST}:${SERVER_PATH}/"

echo ">>> Đẩy lên: ${TARGET}"
echo ">>> Delete-mode: $([[ $DELETE -eq 1 ]] && echo ON || echo OFF)"
echo ">>> (Password SSH sẽ chỉ hỏi 1 lần nhờ ControlMaster.)"

# Tạo thư mục đích trên server nếu chưa có (chỉ khi apply).
if [[ "$APPLY" -eq 1 ]]; then
  $SSH_BASE "${SERVER_USER}@${SERVER_HOST}" "mkdir -p '${SERVER_PATH}'"
fi

rsync "${RSYNC_OPTS[@]}" ./ "$TARGET"

echo ">>> Done."
if [[ "$APPLY" -eq 0 ]]; then
  echo ">>> (Đây là dry-run. Chạy lại với --apply để đồng bộ thật.)"
fi
