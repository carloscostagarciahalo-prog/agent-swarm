#!/bin/bash
# Backup diario de la base de datos. Instálalo así (una vez):
#   sudo cp backup.sh /etc/cron.daily/swarm-backup
#   sudo chmod +x /etc/cron.daily/swarm-backup
#
# Guarda 30 días de histórico, luego borra los más antiguos. La base de
# datos tiene TODO el histórico de agentes, acciones y balances — sin
# esto, un fallo de disco en la VM te hace perder ese historial entero
# (las wallets no se pierden, esas dependen solo de MASTER_SEED, pero el
# registro de qué hizo cada agente y por qué sí).
set -e

APP_DIR="$(dirname "$(readlink -f "$0")")"
BACKUP_DIR="$HOME/backups"

mkdir -p "$BACKUP_DIR"
cp "$APP_DIR/swarm.db" "$BACKUP_DIR/swarm-$(date +%F).db"
find "$BACKUP_DIR" -name "swarm-*.db" -mtime +30 -delete

echo "Backup guardado: $BACKUP_DIR/swarm-$(date +%F).db"
