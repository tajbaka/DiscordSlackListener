.DEFAULT_GOAL := help
.PHONY: help install run daemon backfill catchup test

help:
	@perl -nle'print $$& if m{^[a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## install Python dependencies
	python3.13 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	.venv/bin/python -m playwright install chromium

run: ## run the Discord listener
	.venv/bin/python -m discord_slack_listener

daemon: ## run supervised listener with crash restart
	.venv/bin/python -m discord_slack_listener daemon

backfill: ## backfill recent Discord messages into SQLite
	.venv/bin/python -m discord_slack_listener backfill

catchup: ## backfill until Discord stops yielding older messages
	.venv/bin/python -m discord_slack_listener backfill --catchup

test: ## run tests
	.venv/bin/python -m pytest
