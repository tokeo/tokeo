.PHONY: clean venv outdated devenv proto test fmt docker sdist wheel dist-upload

clean:
	find . -name '*.py[co]' -delete
	find . -type d -name '__pycache__' -delete
	rm -rf .pytest_cache .coverage coverage-report
	rm -rf html
	rm -rf tmp
	mkdir -p tmp/tests
	touch tmp/tests/.gitkeep

venv:
	python -m venv --prompt '> tokeo <' .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	@echo
	@echo "VENV Setup Complete. Now run: source .venv/bin/activate"
	@echo

outdated:
	.venv/bin/pip --disable-pip-version-check list --outdated

devenv:
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements-dev.txt

proto:
	python -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. ./proto/tokeo.proto

doc:
	rm -rf html
	pdoc3 --html tokeo tests
	open http://localhost:9999
	pdoc3 --html --http localhost:9999 tokeo tests

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
cov_report=--cov=tokeo --cov-report=term --cov-report=html:coverage-report
else
cov_report=
endif

# limit the tests to run by files and tests filters
# make test files=test_logging.py tests=logging debug=1
test:
	rm -rf tmp/tests
	mkdir -p tmp/tests
	python -m pytest \
		-v \
		$(allow_print) \
		$(cov_report) \
		--basetemp=tmp/tests \
		$(log_level) \
		-k "$(tests)" \
		tests/$(files)

fmt:
	# align with https://google.github.io/styleguide/pyguide.html
	pyink --pyink-use-majority-quotes --line-length 150 --include "\.py$"" --exclude "\/__pycache__\/" tokeo proto tests docs setup.py

docker: clean
	docker build -t tokeo:latest .

sdist: clean
	rm -rf dist/*
	python setup.py sdist

wheel: clean
	rm -rf dist/*
	python setup.py bdist_wheel

dist-upload:
	twine upload dist/*
