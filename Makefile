.PHONY: install install-local

VERSION := $(shell sed -n 's/^version = "\(.*\)"$$/\1/p' pyproject.toml)
ZSH_PLUGINS ?= $(or $(ZDOTDIR),$(HOME))/.zsh_plugins.txt

# The working copy, in place of anything published: the CLI from this
# checkout, and Fisher and Antidote pointed at it. Run it again after a
# change here, since uv and Fisher copy the files at install.
install-local:
	uv tool install --force --from . git-worktrees
	$(call fish_plugin,$(CURDIR))
	$(call zsh_plugin,$(CURDIR)/zsh/plugins/worktrees)

# This version as published, in place of anything else: the CLI from PyPI
# and the shell plugin from GitHub at its tag, so the two match. The
# Antidote clone goes first, so the next shell clones it at that tag.
install:
	uv tool install --force git-worktrees==$(VERSION)
	$(call fish_plugin,MihaiBojin/worktrees@v$(VERSION))
	-command -v zsh >/dev/null && test -f "$(ZSH_PLUGINS)" && \
		zsh -ic 'antidote purge MihaiBojin/worktrees' </dev/null
	$(call zsh_plugin,MihaiBojin/worktrees branch:v$(VERSION) path:zsh/plugins/worktrees)

# Removes every installed copy of this plugin, published or local, then
# installs $(1). Skipped where fish or Fisher is missing. Fisher reads
# plugin names from stdin when it is not a terminal, hence /dev/null.
define fish_plugin
	if command -v fish >/dev/null && fish -c 'functions -q fisher'; then \
		fish -c ' \
			for p in $$_fisher_plugins; \
				if string match -qri "^mihaibojin/worktrees(@.*)?\$$" -- $$p; or test -f $$p/conf.d/worktrees.fish; \
					fisher remove $$p; or exit 1; \
				end; \
			end; \
			fisher install $$argv[1]' '$(1)' </dev/null; \
	fi
endef

# Replaces this plugin's line in zsh_plugins.txt, published or local, with
# $(1), or appends it. Writes through cat, so a symlinked file stays one.
define zsh_plugin
	if test -f "$(ZSH_PLUGINS)"; then \
		awk -v entry='$(1)' ' \
			tolower($$1) == "mihaibojin/worktrees" || $$1 ~ /\/zsh\/plugins\/worktrees$$/ { \
				if (!done) print entry; done = 1; next \
			} \
			{ print } \
			END { if (!done) print entry }' "$(ZSH_PLUGINS)" >"$(ZSH_PLUGINS).tmp" && \
		cat "$(ZSH_PLUGINS).tmp" >"$(ZSH_PLUGINS)" && rm "$(ZSH_PLUGINS).tmp" && \
		echo "$(ZSH_PLUGINS): $(1)"; \
	fi
endef
