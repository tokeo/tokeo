.PHONY: clean virtualenv test docker dist dist-upload

clean:
	find . -name '*.py[co]' -delete
	find . -type d -name '__pycache__' -delete
	rm -rf tmp

virtualenv:
	virtualenv -q --prompt '> cedra <' .venv
	.venv/bin/pip install -r requirements-dev.txt
	.venv/bin/python setup.py develop
	@echo
	@echo "VirtualENV Setup Complete. Now run: source .venv/bin/activate"
	@echo

doc:
	rm -rf html
	pdoc3 --html braavos tests
	open http://localhost:9999
	pdoc3 --html --http localhost:9999 braavos tests

# check that a log-level is set
# for testing purposes at least use info
ifdef debug
log-level = "$(debug)0"
else
log-level = "9999"
endif

test:
	rm -rf tmp/tests
	mkdir -p tmp/tests
	python -m pytest \
		-v \
		--cov=cedra \
		--cov-report=term \
		--cov-report=html:coverage-report \
		--basetemp=tmp/tests \
		--log-cli-level="$(log-level)" \
		-k "$(tests)" \
		tests/$(files)

fmt.all.fix:
	pyink --pyink-use-majority-quotes --line-length 115 --include "\.py$"" cedra tests docs setup.py

docker: clean
	docker build -t cedra:latest .

dist: clean
	rm -rf dist/*
	python setup.py sdist
	python setup.py bdist_wheel

dist-upload:
	twine upload dist/*

