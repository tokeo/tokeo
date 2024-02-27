.PHONY: clean virtualenv proto test fmt docker dist dist-upload

clean:
	find . -name '*.py[co]' -delete
	find . -type d -name '__pycache__' -delete
	rm -rf coverage-report
	rm -rf html
	rm -rf tmp

virtualenv:
	virtualenv -q --prompt '> tokeo <' .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	@echo
	@echo "VirtualENV Setup Complete. Now run: source .venv/bin/activate"
	@echo

proto:
	python -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. ./proto/tokeo.proto

doc:
	rm -rf html
	pdoc3 --html tokeo tests
	open http://localhost:9999
	pdoc3 --html --http localhost:9999 tokeo tests

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
		--cov=tokeo \
		--cov-report=term \
		--cov-report=html:coverage-report \
		--basetemp=tmp/tests \
		--log-cli-level="$(log-level)" \
		-k "$(tests)" \
		tests/$(files)

fmt:
	# align with https://google.github.io/styleguide/pyguide.html
	pyink --pyink-use-majority-quotes --line-length 115 --include "\.py$"" --exclude "\/__pycache__\/" tokeo proto tests docs setup.py

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

