#!/usr/bin/env bash
set -euo pipefail

PUBLIC_KEY_FILE="${1:-}"
if [[ -z "$PUBLIC_KEY_FILE" || ! -f "$PUBLIC_KEY_FILE" ]]; then
  echo "用法: $0 /path/to/verified-public-key.pub" >&2
  exit 2
fi

install -d -m 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
KEY="$(tr -d '\r\n' < "$PUBLIC_KEY_FILE")"
grep -Fqx "$KEY" /root/.ssh/authorized_keys || printf '%s\n' "$KEY" >> /root/.ssh/authorized_keys

BACKUP="/etc/ssh/sshd_config.codex-backup-$(date +%Y%m%d%H%M%S)"
cp /etc/ssh/sshd_config "$BACKUP"
install -d -m 755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/90-trading-cockpit-hardening.conf <<'EOF'
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF

sshd -t
systemctl reload sshd 2>/dev/null || systemctl reload ssh
echo "SSH 已切换为密钥登录。请保持当前会话，并在新终端验证密钥可登录后再退出。备份: $BACKUP"
