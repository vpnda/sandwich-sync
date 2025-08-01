module github.com/vpnda/sandwich-sync

go 1.23.0

toolchain go1.23.5

require (
	github.com/Rhymond/go-money v1.0.14
	github.com/goccy/go-yaml v1.17.1
	github.com/icco/lunchmoney v0.4.1
	github.com/mattn/go-sqlite3 v1.14.22
	github.com/rs/zerolog v1.34.0
	github.com/samber/lo v1.50.0
	github.com/spf13/cobra v1.9.1
	github.com/stretchr/testify v1.10.0
	github.com/vpnda/scotiafetch v0.0.0-20250801182250-05046ca13d4f
	github.com/vpnda/wsfetch v0.1.2-0.20250515155945-15d5c1997864
	golang.org/x/text v0.25.0
)

require (
	github.com/Khan/genqlient v0.8.0 // indirect
	github.com/davecgh/go-spew v1.1.1 // indirect
	github.com/gabriel-vasile/mimetype v1.4.8 // indirect
	github.com/go-playground/locales v0.14.1 // indirect
	github.com/go-playground/universal-translator v0.18.1 // indirect
	github.com/go-playground/validator/v10 v10.26.0 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/inconshreveable/mousetrap v1.1.0 // indirect
	github.com/leodido/go-urn v1.4.0 // indirect
	github.com/mattn/go-colorable v0.1.13 // indirect
	github.com/mattn/go-isatty v0.0.19 // indirect
	github.com/pmezard/go-difflib v1.0.0 // indirect
	github.com/rogpeppe/go-internal v1.14.1 // indirect
	github.com/spf13/pflag v1.0.6 // indirect
	github.com/vektah/gqlparser/v2 v2.5.27 // indirect
	go.uber.org/multierr v1.11.0 // indirect
	go.uber.org/zap v1.27.0 // indirect
	golang.org/x/crypto v0.37.0 // indirect
	golang.org/x/net v0.39.0 // indirect
	golang.org/x/sys v0.32.0 // indirect
	gopkg.in/yaml.v3 v3.0.1 // indirect
)

replace github.com/icco/lunchmoney v0.4.1 => github.com/vpnda/lunchmoney v0.5.4
