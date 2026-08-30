COMPOSE_FILES := docker-compose.yml $(wildcard services/*.yml)
COMPOSE := docker compose $(foreach f,$(COMPOSE_FILES), -f $(f))

.PHONY: up down restart ps logs pull

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
