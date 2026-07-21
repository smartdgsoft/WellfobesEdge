# Wellfobes fleet monorepo — common tasks.
# The 'check' target is the guard that keeps edge/center/shared loosely coupled.

.PHONY: install check test test-unit test-edge test-center build-edge build-full clean

install:            ## install the shared contract + dev deps
	pip install -e ./shared paho-mqtt psycopg2-binary --break-system-packages

check:              ## FAIL if edge imports center (or shared imports either)
	python3 tools/check_boundaries.py

test-unit:          ## shared contract unit tests (no broker)
	python3 shared/tests/test_contract.py

test-edge:          ## edge gateway end-to-end (needs a broker on $$MQTT_PORT)
	python3 edge/tests/test_gateway_e2e.py        # SKU-1 live path + RBE
	python3 edge/tests/test_durability_e2e.py     # SKU-2 store-and-forward: ack/redeliver/dedupe
	python3 edge/tests/test_tagset_control.py      # config-driven tag allowlist

test-center:        ## historian decode/identity end-to-end (needs a broker)
	python3 center/tests/test_historian_e2e.py

test: check test-unit test-edge test-center   ## everything

build-edge:         ## build ONLY the edge image (proves standalone)
	docker build -f edge/Dockerfile -t wellfobes-edge .

build-full:         ## build edge + center images
	docker compose -f docker-compose.full.yml build

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
