# -*- coding: utf-8 -*-
"""临时探测：远程读取鸿蒙设备的形态分类相关属性。"""

import json
import urllib.request

WORKER = "http://192.168.0.105:8088"
HDC = r'"D:\Test Worker\tools\hdc\hdc.exe"'
UDID = "3QC0124A10000066"

CMDS = [
    "param get const.product.form",
    "param get const.product.family",
    "param get const.product.model",
    "param get const.product.name",
    "param get const.build.characteristics",
    "param get const.product.devicetype",
    "param get const.ohos.apiversion",
    "param get const.product.software.version",
    "param get const.product.deviceType",
]

for c in CMDS:
    payload = json.dumps(
        {
            "platform": "windows",
            "device_id": None,
            "actions": [
                {"action_type": "cmd_exec", "value": f'{HDC} -t {UDID} shell "{c}"'}
            ],
        }
    ).encode()
    req = urllib.request.Request(
        WORKER + "/task/execute",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    a = (r.get("actions") or [{}])[0]
    print(c, "->", repr(a.get("stdout") or a.get("output")))
