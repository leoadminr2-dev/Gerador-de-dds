# -*- coding: utf-8 -*-
from __future__ import print_function
import json, os, sys, time
from urllib.request import urlopen
port = os.environ.get("DDS_PORT", "8766")
expected_version = "1.2.2-local"
url = "http://127.0.0.1:{0}/api/health".format(port)
deadline = time.time() + 25.0
last_error = None
while time.time() < deadline:
    try:
        response = urlopen(url, timeout=1.5)
        try:
            payload = json.loads(response.read().decode("utf-8"))
            if response.getcode() == 200 and payload.get("status") == "ok" and payload.get("version") == expected_version:
                print("Servico DDS respondendo em {0}".format(url))
                sys.exit(0)
            last_error = "Versao inesperada do servico: {0}; esperada: {1}.".format(payload.get("version"), expected_version)
        finally:
            response.close()
    except Exception as exc:
        last_error = str(exc)
    time.sleep(0.5)
print("Falha ao confirmar o servico DDS em {0}.".format(url))
if last_error:
    print("Ultimo detalhe: {0}".format(last_error))
sys.exit(1)
