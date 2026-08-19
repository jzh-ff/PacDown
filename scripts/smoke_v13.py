"""v1.3 多用户+新功能全流程冒烟：注册/继承/隔离/权限/字幕/备份/编辑/toast。"""
import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8301"


def c():
    return httpx.Client(base_url=BASE, timeout=60, trust_env=False)


def check(name, cond, extra=""):
    print(("✓ " if cond else "✗ ") + name + (f"  {extra}" if extra and not cond else ""))
    return cond


async def main():
    ok = True
    # ---- 1. 未登录访问 → 401 ----
    with c() as cl:
        r = cl.get("/api/tasks")
        ok &= check("未登录 401", r.status_code == 401, str(r.status_code))

        # 注册前先插入存量数据（模拟旧库 user_id=0 的孤儿记录）
        import sqlite3, os
        db_path = os.path.join(os.environ["TEMP"], "pactest", "downloads", "..", "data", "metadata.db")
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO videos(platform, video_id, source_url, title, status, user_id, created_at)"
                     " VALUES('direct','legacy_1','http://x','存量数据','done',0,datetime('now'))")
        conn.commit()

        # ---- 2. 首个注册 = admin + 继承存量数据 ----
        r = cl.post("/api/auth/register", json={"username": "站长", "password": "admin123"})
        ok &= check("首个注册成为 admin", r.status_code == 200 and r.json()["user"]["role"] == "admin",
                    r.text[:120])
        admin_cookies = r.cookies

        row = conn.execute("SELECT user_id FROM videos WHERE video_id='legacy_1'").fetchone()
        conn.close()
        ok &= check("存量数据被首个 admin 继承", row and row[0] == 1, f"user_id={row}")

        # ---- 3. 注册第二个用户 → user 角色 ----
        cl.cookies.clear()
        r = cl.post("/api/auth/register", json={"username": "访客甲", "password": "user123"})
        ok &= check("第二个注册为普通用户", r.status_code == 200 and r.json()["user"]["role"] == "user",
                    r.text[:120])
        user_cookies = r.cookies

        # ---- 4. admin 专属：普通用户访问设置/统计/备份 → 403 ----
        r = cl.get("/api/config")
        ok &= check("普通用户访问配置 403", r.status_code == 403, str(r.status_code))
        r = cl.get("/api/stats/visits")
        ok &= check("普通用户访问统计 403", r.status_code == 403, str(r.status_code))
        r = cl.get("/api/backup/download")
        ok &= check("普通用户访问备份 403", r.status_code == 403, str(r.status_code))

        # ---- 5. 普通用户建任务，admin 看不到（数据隔离） ----
        r = cl.post("/api/download", json={"text": "https://www.w3schools.com/html/mov_bbb.mp4#direct;name=用户甲的文件", "options": {}})
        user_vid = r.json()["results"][0]["id"]
        import time
        for _ in range(30):
            time.sleep(1)
            t = [x for x in cl.get("/api/tasks").json()["tasks"] if x["id"] == user_vid]
            if not t or t[0]["status"] in ("done", "failed"):
                break
        ok &= check("普通用户下载完成", t and t[0]["status"] == "done", str(t))
        # admin 视角看不到用户甲的任务
        cl.cookies.clear()
        cl.cookies.update(admin_cookies)
        items = cl.get("/api/history?size=50").json()["items"]
        ok &= check("admin 看不到用户甲记录", all(i["id"] != user_vid for i in items),
                    f"admin items={len(items)}")
        # 跨用户删除 → 403（admin 删用户甲记录）
        cl.cookies.clear()
        cl.cookies.update(admin_cookies)
        r = cl.delete(f"/api/history/{user_vid}")
        ok &= check("admin 删用户甲记录 403", r.status_code == 403, str(r.status_code))
        cl.cookies.clear()
        cl.cookies.update(user_cookies)

        # ---- 6. 下载目录用户名层级 ----
        v = cl.get(f"/api/history/{user_vid}").json()
        ok &= check("文件路径含用户名层级", "访客甲" in v.get("file_path", ""), v.get("file_path", ""))

        # ---- 7. 元数据编辑 ----
        r = cl.patch(f"/api/history/{user_vid}", json={"title": "改名了", "author": "新作者"})
        ok &= check("元数据编辑", r.status_code == 200)
        v = cl.get(f"/api/history/{user_vid}").json()
        ok &= check("标题已更新", v["title"] == "改名了", v["title"])

        # ---- 8. 弹幕转 ASS（造假弹幕 XML）----
        import pathlib
        vpath = pathlib.Path(v["file_path"])
        xml = vpath.with_suffix(".xml")
        xml.write_text('<?xml version="1.0"?><i><chatserver>t</chatserver><maxlimit>5000</maxlimit>'
                       '<d p="1000,1,25,16777215,1" >第一条弹幕</d>'
                       '<d p="2000,5,25,16777215,1" >第二条滚动弹幕</d>'
                       '<d p="3000,4,25,16777215,1" >顶部弹幕</d></i>', encoding="utf-8")
        r = cl.post("/api/toolbox/jobs", json={"kind": "danmaku2ass", "video_id": user_vid, "params": {}})
        ok &= check("danmaku2ass 入队", r.status_code == 200, r.text[:100])
        jid = r.json()["id"]
        for _ in range(20):
            time.sleep(1)
            j = [x for x in cl.get("/api/toolbox/jobs").json()["items"] if x["id"] == jid]
            if j and j[0]["status"] in ("done", "failed"):
                break
        ok &= check("danmaku2ass 完成", j and j[0]["status"] == "done", str(j and j[0].get("error")))
        if j and j[0]["status"] == "done":
            f = cl.get(f"/api/toolbox/jobs/{jid}/file")
            ass = f.text
            ok &= check("ASS 内容有效", "ScriptType: v4.00+" in ass and "第一条弹幕" in ass)

        # 用户甲清理自己的记录
        r = cl.delete(f"/api/history/{user_vid}")
        ok &= check("用户甲可删自己记录", r.status_code == 200, str(r.status_code))

        # ---- 9. B站字幕接口（无字幕优雅跳过）----
        import httpx as _hx
        cl2 = _hx.Client(base_url=BASE, timeout=300, cookies=cl.cookies, trust_env=False)
        r = cl2.post("/api/download", json={"text": "https://www.bilibili.com/video/BV1GJ411x7h7", "options": {"download_subtitle": True}})
        bvid = r.json()["results"][0]["id"]
        for _ in range(120):
            time.sleep(1)
            t = [x for x in cl2.get("/api/tasks").json()["tasks"] if x["id"] == bvid]
            if not t or t[0]["status"] in ("done", "failed"):
                break
        v = cl2.get(f"/api/history/{bvid}").json()
        ok &= check("B站下载完成（无字幕视频优雅跳过）", v["status"] == "done", v.get("error", ""))
        cl2.close()

        # ---- 10. 备份下载 + 恢复（admin）----
        cl.cookies.clear()
        cl.cookies.update(admin_cookies)
        r = cl.get("/api/backup/download")
        ok &= check("备份下载", r.status_code == 200 and len(r.content) > 1000, f"{len(r.content)}B")
        backup = r.content
        r = cl.post("/api/backup/restore", files={"file": ("bk.zip", backup, "application/zip")})
        ok &= check("备份恢复", r.status_code == 200, r.text[:120])

        # ---- 11. 规则/订阅隔离 ----
        r = cl.post("/api/rules", json={"name": "站长规则", "match_type": "all",
                                        "actions": [{"kind": "mp3", "params": {}}]})
        ok &= check("admin 建规则", r.status_code == 200)
        cl.cookies.clear()
        cl.cookies.update(user_cookies)
        rules = cl.get("/api/rules").json()["items"]
        ok &= check("用户甲看不到 admin 规则", len(rules) == 0, str(len(rules)))

        # ---- 12. 注册开关（admin 关闭后 403）----
        cl.cookies.clear()
        cl.cookies.update(admin_cookies)
        cl.post("/api/config", json={"allow_register": False})
        cl.cookies.clear()
        r = cl.post("/api/auth/register", json={"username": "新人", "password": "pass123"})
        ok &= check("关闭注册后新注册 403", r.status_code == 403, str(r.status_code))
        cl.post("/api/config", json={"allow_register": True})

        # ---- 13. 登出 ----
        r = cl.post("/api/auth/logout")
        ok &= check("登出", r.status_code == 200)

    print("\n" + ("全部通过 🎉" if ok else "存在失败项 ❌"))
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
