.PHONY: clean venv outdated devenv proto test fmt docker sdist wheel dist-upload

clean:
	find . -name '*.py[co]' -delete
	find . -type d -name '__pycache__' -delete
	rm -rf .pytest_cache .coverage coverage-report
	rm -rf html
	rm -rf tmp
	rm -rf dist/*
	rm -rf build/
	mkdir -p tmp/tests
	touch tmp/tests/.gitkeep

venv:
	python -m venv --prompt '> {{ app_label }} <' .venv
	.venv/bin/pip install --upgrade pip
	@echo
	@echo
	@echo "___________________________________________________"
	@echo
	@echo
	@echo "VENV Setup Complete!"
	@echo "  activate now using: \`source .venv/bin/activate\`"
	@echo
	@echo "With active venv install the app:"
	@echo "  \`make dev\` or \`make prod\`"
	@echo
{% if feature_grpc == "Y" %}
	@echo "Generate the python files from proto:"
	@echo "  \`make proto\`"
	@echo
{% endif %}
	@echo "Run your app the first time:"
	@echo "  \`{{app_label}} --help\`"
	@echo

outdated:
	.venv/bin/pip --disable-pip-version-check list --outdated

dev:
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e .
	.venv/bin/pip install -e .[dev]

prod:
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e .

proto:
	python -m grpc_tools.protoc -I./ --python_out=. --pyi_out=. --grpc_python_out=. ./{{ app_label }}/core/grpc/proto/{{ app_label }}.proto

doc:
	rm -rf html
	pdoc3 --html {{ app_label }} tests
	open http://localhost:9999
	pdoc3 --html --http localhost:9999 {{ app_label }} tests

# check for verbosity
ifdef verbose
verbosity=${verbose}
else
verbosity=v
endif

# check for log-level and enable print statement outputs on debug
ifdef debug
allow_print=-s
log_level=--log-cli-level=$(debug)0
else
allow_print=
log_level=
endif

# check for test with coverage reports
ifdef cov
cov_report=--cov={{ app_label }} --cov-report=term --cov-report=html:coverage-report
else
cov_report=
endif

# check for files
ifndef files
files=tests/
endif

# limit the tests to run by files and tests filters
# make test files=test_logging.py tests=logging debug=1
test:
	rm -rf tmp/tests
	mkdir -p tmp/tests
	touch tmp/tests/.gitkeep
	CEMENT_LOG=0 \
	python -m pytest \
		-${verbosity} \
		$(allow_print) \
		$(cov_report) \
		--basetemp=tmp/tests \
		$(log_level) \
		-k "$(tests)" \
		$(files)

# check for files
ifndef sources
sources={{ app_label }} docs tests
endif

fmt:
	# align with https://google.github.io/styleguide/pyguide.html
	pyink --pyink-use-majority-quotes --line-length 139 --include "\.py" --exclude="/(\.git|__pycache__)/" $(sources)

lint:
	# align with https://google.github.io/styleguide/pyguide.html
	flake8 --max-line-length 140 --max-doc-length 84 --extend-ignore "" --exclude "*/grpc/proto/*_pb2*.py,.git,__pycache__" $(sources)

docker: clean
	docker build -t {{ app_label }}:latest .

sdist: clean
	python -m build --sdist

wheel: clean
	python -m build --wheel

dist-upload:
	twine upload dist/*
