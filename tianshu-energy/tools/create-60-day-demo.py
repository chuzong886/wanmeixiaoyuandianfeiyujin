"""Create a deterministic, isolated 60-day Tianshu demonstration database."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import shutil
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from energy import Energy, TZ, jdump, tariff_default  # noqa: E402


USER = {"id": "demo", "username": "演示管理员", "role": "demo"}
SPACE_ID = "space-demo-60-days"


DEVICE_SPECS = [
    {
        "id": "demo-press-1",
        "name": "冲压机床-1",
        "area": "冲压机床车间",
        "device_type": "socket",
        "nominal_w": 9_000,
        "threshold_w": 10_800,
        "phases": 3,
        "base": 0.06,
        "work": 0.72,
        "shift": 0.3,
    },
    {
        "id": "demo-press-meter",
        "name": "机床智能电表",
        "area": "冲压机床车间",
        "device_type": "control_meter",
        "nominal_w": 18_000,
        "threshold_w": 20_000,
        "phases": 3,
        "base": 0.08,
        "work": 0.66,
        "shift": 1.0,
    },
    {
        "id": "demo-press-2",
        "name": "冲压机床-2",
        "area": "冲压机床车间",
        "device_type": "socket",
        "nominal_w": 11_000,
        "threshold_w": 13_200,
        "phases": 3,
        "base": 0.05,
        "work": 0.75,
        "shift": 1.7,
    },
    {
        "id": "demo-compressor",
        "name": "空压机回路",
        "area": "动力站房",
        "device_type": "breaker",
        "nominal_w": 15_000,
        "threshold_w": 17_500,
        "phases": 3,
        "base": 0.1,
        "work": 0.58,
        "shift": 2.2,
    },
    {
        "id": "demo-lighting",
        "name": "车间照明回路",
        "area": "公共区域",
        "device_type": "breaker",
        "nominal_w": 4_800,
        "threshold_w": 5_500,
        "phases": 1,
        "base": 0.03,
        "work": 0.82,
        "shift": 2.8,
    },
    {
        "id": "demo-office",
        "name": "办公区插座",
        "area": "办公区域",
        "device_type": "socket",
        "nominal_w": 3_600,
        "threshold_w": 4_300,
        "phases": 1,
        "base": 0.04,
        "work": 0.55,
        "shift": 3.4,
    },
    {
        "id": "demo-essential",
        "name": "消防与监控保障回路",
        "area": "保障区域",
        "device_type": "breaker",
        "nominal_w": 2_400,
        "threshold_w": 3_000,
        "phases": 1,
        "base": 0.48,
        "work": 0.12,
        "shift": 4.0,
        "protected": True,
    },
]


def power_for(spec: dict, when: dt.datetime, day_index: int) -> float:
    hour = when.hour + 0.5
    weekday = when.weekday() < 5
    occupied = weekday and 7 <= hour < 19
    shoulder = weekday and (6 <= hour < 7 or 19 <= hour < 21)
    factor = spec["base"] + (spec["work"] if occupied else spec["work"] * 0.2 if shoulder else 0)
    if not weekday and spec["id"] not in ("demo-essential", "demo-lighting"):
        factor *= 0.58
    if spec["id"] == "demo-lighting":
        factor = 0.72 if 6 <= hour < 20 else 0.035
        if not weekday:
            factor *= 0.42
    if spec["id"] == "demo-essential":
        factor = 0.58 + 0.025 * math.sin(hour * 0.8)
    daily_weather = 1 + 0.055 * math.sin(day_index / 5.2)
    texture = 0.045 * math.sin(hour * 1.13 + spec["shift"]) + 0.025 * math.sin(day_index * 1.71 + spec["shift"])
    # Two visible but believable historical events for charts and incident history.
    if spec["id"] == "demo-press-meter" and day_index == 47 and 14 <= hour < 17:
        factor = 1.24
    if spec["id"] == "demo-compressor" and day_index == 54 and 1 <= hour < 4:
        factor = 0.78
    return round(max(spec["nominal_w"] * 0.02, spec["nominal_w"] * factor * daily_weather * (1 + texture)), 2)


def add_incident(app: Energy, device_id: str, key: str, level: str, title: str, created: float, closed: bool) -> None:
    recovered = created + 38 * 60
    app.db.execute(
        """INSERT INTO alerts(
               id,space_id,device_id,key,level,title,message,state,created,updated,ack_by,
               machine_state,handling_state,assigned_to,occurrences,recovered_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uuid.uuid4().hex,
            SPACE_ID,
            device_id,
            key,
            level,
            title,
            "模拟历史事件：用于演示告警确认、恢复与闭环处理。",
            "resolved",
            created,
            recovered if closed else recovered + 60,
            "演示管理员",
            "normal",
            "closed" if closed else "pending",
            "",
            1,
            recovered,
        ),
    )


def create_database(target: Path) -> dict:
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"目标目录不是空目录，已停止以避免覆盖：{target}")
    target.mkdir(parents=True, exist_ok=True)
    app = Energy(target, seed=False)
    now = dt.datetime.now(TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    history_start = today - dt.timedelta(days=60)
    end_hour = now.replace(minute=0, second=0, microsecond=0)
    try:
        tariff = tariff_default()
        tariff.update({"monthly_budget": 13_500, "mode": "time_of_use", "note": "60天演示环境分时电价（模拟）"})
        app.db.execute(
            "INSERT INTO spaces(id,name,description,tariff,created) VALUES(?,?,?,?,?)",
            (
                SPACE_ID,
                "【60天演示】智能制造园区",
                "全部设备、曲线、费用与事件均为软件生成的模拟数据，不代表真实计量或控制结果。",
                jdump(tariff),
                history_start.timestamp(),
            ),
        )
        app.db.execute(
            "INSERT INTO tariff_versions(space_id,effective,tariff) VALUES(?,?,?)",
            (SPACE_ID, history_start.timestamp(), jdump(tariff)),
        )

        totals = {}
        current_values = {}
        for spec in DEVICE_SPECS:
            protected = int(spec.get("protected", False))
            kind = "meter" if spec["device_type"] in ("readonly_meter", "control_meter") else "circuit" if spec["device_type"] == "breaker" else "appliance"
            app.db.execute(
                """INSERT INTO devices(
                       id,space_id,name,area,kind,source,nominal_w,threshold_w,protected,controllable,
                       phases,nominal_voltage,counted,device_type,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    spec["id"], SPACE_ID, spec["name"], spec["area"], kind, "simulated",
                    spec["nominal_w"], spec["threshold_w"], protected, int(not protected),
                    spec["phases"], 380 if spec["phases"] == 3 else 220, 1,
                    spec["device_type"], history_start.timestamp(),
                ),
            )
            app.db.execute(
                "INSERT INTO scopes(device_id,effective,counted,parent_id) VALUES(?,?,1,'')",
                (spec["id"], history_start.timestamp()),
            )
            rows = []
            cursor = history_start
            total_wh = 0.0
            while cursor <= end_hour:
                day_index = (cursor.date() - history_start.date()).days
                watts = power_for(spec, cursor, day_index)
                rows.append((spec["id"], int(cursor.timestamp()), watts, 3600, 1))
                total_wh += watts
                cursor += dt.timedelta(hours=1)
            app.db.executemany("INSERT INTO hourly VALUES(?,?,?,?,?)", rows)
            current = power_for(spec, now, 60)
            current_values[spec["id"]] = current
            samples = []
            now_ts = now.timestamp()
            for seconds_ago in range(179, -1, -1):
                ts = now_ts - seconds_ago
                watts = max(0, current * (1 + 0.018 * math.sin(seconds_ago / 8 + spec["shift"])))
                samples.append((spec["id"], ts, watts, 380 if spec["phases"] == 3 else 220, 0.94))
            app.db.executemany("INSERT INTO samples VALUES(?,?,?,?,?)", samples)
            app.db.execute(
                "UPDATE devices SET total_kwh=?,power_w=?,voltage=?,pf=?,last_seen=?,last_sample=?,sequence=? WHERE id=?",
                (total_wh / 1000, current, 380 if spec["phases"] == 3 else 220, 0.94, now_ts, now_ts, 180, spec["id"]),
            )
            totals[spec["id"]] = round(total_wh / 1000, 2)

        for device_id in ("demo-press-1", "demo-press-2", "demo-compressor", "demo-lighting", "demo-office"):
            app.save_schedule(USER, {"device_id": device_id, "at": "07:30", "desired": True, "weekdays": [0, 1, 2, 3, 4]})
            app.save_schedule(USER, {"device_id": device_id, "at": "19:10", "desired": False, "weekdays": [0, 1, 2, 3, 4]})

        for index, (name, area) in enumerate(
            [
                ("冲压车间东侧", "冲压机床车间"),
                ("冲压车间西侧", "冲压机床车间"),
                ("动力站房", "动力站房"),
                ("园区公共区域", "公共区域"),
            ],
            start=1,
        ):
            app.save_camera(USER, {"space_id": SPACE_ID, "name": name, "area": area, "kind": "illustration", "url": ""})

        rules = [
            ("rule-demo-power", "机床持续功率预警", {"metric": "power", "device_id": "demo-press-meter", "threshold": 20_000, "duration": 120, "recovery": 300, "hysteresis": 800, "cooldown": 1800, "level": "warning"}),
            ("rule-demo-night", "空压机非生产时段运行", {"metric": "off_hours", "device_id": "demo-compressor", "threshold": 2_000, "duration": 300, "recovery": 300, "hysteresis": 200, "cooldown": 3600, "level": "warning", "start": "07:00", "end": "20:00"}),
            ("rule-demo-cost", "月度费用接近预算", {"metric": "monthly_cost", "threshold": 12_000, "duration": 0, "recovery": 600, "hysteresis": 300, "cooldown": 86400, "level": "warning"}),
        ]
        app.db.executemany(
            "INSERT INTO rules(id,space_id,name,config,enabled) VALUES(?,?,?,?,1)",
            [(rid, SPACE_ID, name, jdump(config)) for rid, name, config in rules],
        )

        add_incident(app, "demo-press-meter", "history:press-overload", "critical", "机床功率持续超限", (today - dt.timedelta(days=13, hours=-14)).timestamp(), True)
        add_incident(app, "demo-compressor", "history:compressor-night", "warning", "非生产时段发现持续负载", (today - dt.timedelta(days=6, hours=-1)).timestamp(), True)
        add_incident(app, "demo-lighting", "history:lighting-review", "warning", "照明异常已恢复，待复核", (today - dt.timedelta(days=2, hours=-20)).timestamp(), False)

        app.audit("system", "seed_demo_60_days", SPACE_ID, "生成60个完整自然日及今日小时级模拟数据；不连接物理设备")
        app.db.commit()
        report = app.reports(SPACE_ID, 30)
        prediction = app.prediction(SPACE_ID)
        row_count = app.one("SELECT COUNT(*) n FROM hourly")["n"]
        complete_days = app.one(
            "SELECT COUNT(DISTINCT date(hour,'unixepoch','+8 hours')) n FROM hourly WHERE hour>=? AND hour<?",
            (history_start.timestamp(), today.timestamp()),
        )["n"]
        if complete_days != 60 or not prediction["available"]:
            raise RuntimeError("演示数据完整性校验失败")
        return {
            "space": "【60天演示】智能制造园区",
            "generated_at": now.isoformat(),
            "history_start": history_start.isoformat(),
            "complete_days": complete_days,
            "hourly_rows": row_count,
            "devices": len(DEVICE_SPECS),
            "schedules": app.one("SELECT COUNT(*) n FROM schedules")["n"],
            "rules": len(rules),
            "incidents": app.one("SELECT COUNT(*) n FROM alerts")["n"],
            "cameras": 4,
            "prediction_available": prediction["available"],
            "last_30_days_kwh": round(report["total_kwh"], 2),
            "device_totals_kwh": totals,
            "notice": "此目录中的全部业务数据均为模拟数据。",
        }
    finally:
        app.close()


def write_entry_files(bundle: Path, manifest: dict) -> None:
    launcher = bundle / "启动60天演示环境.cmd"
    launcher.write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"%~dp0app\\Tianshu.Energy.exe\" --data-dir \"%~dp0demo-data\"\r\n",
        encoding="ascii",
    )
    readme = bundle / "使用说明.txt"
    readme.write_text(
        "天枢能源 · 60天独立演示环境\n\n"
        "双击“启动60天演示环境.cmd”即可进入。\n"
        "本环境不会读取或覆盖电脑中已有的天枢数据。\n"
        "内含60个完整自然日的小时级数据、7台设备、10项计划任务、3条规则、3个历史事件和4个监控场景。\n"
        "所有数据均为软件生成的模拟数据，不代表真实电量、费用、故障或控制结果。\n",
        encoding="utf-8-sig",
    )
    (bundle / "演示数据清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--app", type=Path)
    args = parser.parse_args()
    bundle = args.output.resolve()
    if bundle.exists() and any(bundle.iterdir()):
        raise SystemExit(f"输出目录不是空目录，已停止以避免覆盖：{bundle}")
    bundle.mkdir(parents=True, exist_ok=True)
    app_source = args.app.resolve() if args.app else ROOT / "dist" / "windows-1.0.0"
    if not (app_source / "Tianshu.Energy.exe").is_file():
        raise SystemExit(f"找不到可运行的天枢程序：{app_source}")
    shutil.copytree(app_source, bundle / "app")
    manifest = create_database(bundle / "demo-data")
    write_entry_files(bundle, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
