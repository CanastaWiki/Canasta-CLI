APP_NAME="canasta"
GOOS=$(shell go env GOOS)
GOARCH=$(shell go env GOARCH)
OUTPUT=build/${APP_NAME}-${GOOS}-${GOARCH}

build:
		@echo "Building ${APP_NAME} for ${GOOS}/${GOARCH}..."
		@./build.sh
		@go mod tidy
		@go mod verify
		@echo "Build complete: ${OUTPUT}"
		@echo "To run the CLI, use \033[0;32m./${APP_NAME}\033[0m or run 'make install' to install system-wide"

install: build
		@sudo cp ${OUTPUT} /usr/local/bin/${APP_NAME}
		@echo "Installed to: /usr/local/bin/${APP_NAME}"

clean:
		@echo "Cleaning build artifacts..."
		@rm -rf build/
		@echo "Clean complete."

prepare-lint:
		@if ! hash golangci-lint 2>/dev/null; then printf "\e[1;36m>> Installing golangci-lint (this may take a while)...\e[0m\n"; go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest; fi

lint:
		@printf "\e[1;36m>> golangci-lint\e[0m\n"
		@golangci-lint run ./...

test-cover:
		go test -coverprofile=unit-coverage.out ./...
		@echo "Unit coverage written to unit-coverage.out"

integration-cover:
		INTEGRATION_COVER_PROFILE=integration-coverage.out \
		go test -tags integration -timeout 20m -v ./tests/integration/...
		@echo "Integration coverage written to integration-coverage.out"

coverage-report: unit-coverage.out integration-coverage.out
		@echo "mode: set" > combined-coverage.out
		@tail -n +2 unit-coverage.out >> combined-coverage.out
		@tail -n +2 integration-coverage.out >> combined-coverage.out
		@go tool cover -func=combined-coverage.out | tail -1
		@echo "Full report: go tool cover -func=combined-coverage.out"
		@echo "HTML report: go tool cover -html=combined-coverage.out -o coverage.html"

help:
		@printf "\n"
		@printf "\e[1mUsage:\e[0m\n"
		@printf "  make \e[36m<target>\e[0m\n"
		@printf "\n"
		@printf "\e[1mGeneral\e[0m\n"
		@printf "  \e[36mhelp\e[0m                     Display this help.\n"
		@printf "\n"
		@printf "\e[1mBuild\e[0m\n"
		@printf "  \e[36mbuild\e[0m                    Build binary (outputs to build/ directory and creates symlink).\n"
		@printf "  \e[36minstall\e[0m                  Build and install to /usr/local/bin/ (requires sudo).\n"
		@printf "  \e[36mclean\e[0m                    Remove build artifacts and symlink.\n"
		@printf "\n"
		@printf "\e[1mTest\e[0m\n"
		@printf "  \e[36mprepare-lint\e[0m         Install golangci-lint. This is used in CI, you should probably install golangci-lint using your package manager.\n"
		@printf "  \e[36mlint\e[0m                     Run lint.\n"
		@printf "\n"
		@printf "\e[1mCoverage\e[0m\n"
		@printf "  \e[36mtest-cover\e[0m               Run unit tests with coverage.\n"
		@printf "  \e[36mintegration-cover\e[0m        Run integration tests with coverage (requires Docker).\n"
		@printf "  \e[36mcoverage-report\e[0m          Merge unit and integration profiles and show combined coverage.\n"
		@printf "\n"

.PHONY: build install clean prepare-lint lint test-cover integration-cover coverage-report help
