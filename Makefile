.PHONY: lint test check
lint:
	python3 -I -m py_compile loot.py clearline.py mech_bot.py mechfight.py tests/test_bot.py
test:
	python3 -I tests/test_bot.py
check: lint test
