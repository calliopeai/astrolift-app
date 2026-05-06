.PHONY: dev build lint fmt test codegen clean

dev:
	npm run dev

build:
	npm run build

lint:
	npm run lint

fmt:
	npm run format

fmt-check:
	npm run format:check

test:
	@echo "No test runner configured yet."

codegen:
	@echo "GraphQL codegen not yet configured. See bootstrap.md."

clean:
	rm -rf .next out node_modules
