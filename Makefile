COMPOSE_FILES := docker-compose.yml $(wildcard services/*.yml)
COMPOSE := docker compose $(foreach f,$(COMPOSE_FILES), -f $(f))

.PHONY: up down restart ps logs pull backup backup-list restore restore-drill

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) down
	$(COMPOSE) up -d

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100

pull:
	$(COMPOSE) pull

backup:
	./scripts/backup.sh

backup-list:
	@set -a; . ./.env; set +a; \
	if [ "$${BACKUP_RESTIC_REPOSITORY#/}" != "$$BACKUP_RESTIC_REPOSITORY" ]; then \
	  mount="-v $$BACKUP_RESTIC_REPOSITORY:/repo"; repo="/repo"; \
	else \
	  mount=""; repo="$$BACKUP_RESTIC_REPOSITORY"; \
	fi; \
	docker run --rm $$mount -e RESTIC_REPOSITORY="$$repo" -e RESTIC_PASSWORD="$$BACKUP_RESTIC_PASSWORD" \
	  restic/restic:latest snapshots

restore:
	./scripts/restore.sh $(SNAPSHOT)

# Safe to run anytime: restores into a throwaway directory and never
# touches the live stack, unlike `restore` above. Run quarterly.
restore-drill:
	./scripts/restore_drill.sh
