import re
from datetime import datetime

ACCESS = re.compile(
    r'^(?P<ip>\S+) - \S+ \[(?P<time>[^]]+)\] "(?P<request>(?:\\.|[^"\\])*)" (?P<status>\d{3}) (?P<bytes>\d+|-) "(?P<referer>(?:\\.|[^"\\])*)" "(?P<ua>(?:\\.|[^"\\])*)" rt=(?P<rt>[\d.]+|-)\s*$'
)
ERROR = re.compile(
    r"^(?P<time>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(?P<level>[^]]+)\] \d+#\d+: (?:\*\d+ )?(?P<message>.*)$"
)
# Nginx also logs arbitrary bytes sent to an HTTP port in $request.
# Only extract a method from a complete request line, within the DB limit.
REQUEST_LINE = re.compile(
    r"(?P<method>[!#$%&'*+.^_`|~0-9A-Za-z-]{1,32}) [^\s]+ HTTP/\d+\.\d+"
)


def parse(raw, kind):
    if kind == "access":
        m = ACCESS.match(raw)
        if not m:
            raise ValueError("不符合 nginx main access 格式")
        g = m.groupdict()
        # Discard the offset without changing the printed clock time.
        time = datetime.strptime(g["time"], "%d/%b/%Y:%H:%M:%S %z").replace(tzinfo=None)
        request_line = REQUEST_LINE.fullmatch(g["request"])
        return {
            "timestamp": time,
            "level": "access",
            "ip": g["ip"],
            "method": request_line["method"] if request_line else None,
            "request": g["request"],
            "status": int(g["status"]),
            "body_bytes": int(g["bytes"]) if g["bytes"] != "-" else 0,
            "request_time": float(g["rt"]) if g["rt"] != "-" else None,
            "referer": g["referer"],
            "user_agent": g["ua"],
        }
    m = ERROR.match(raw)
    if not m:
        raise ValueError("不符合 nginx error 格式（可能为续行）")
    g = m.groupdict()
    time = datetime.strptime(g["time"], "%Y/%m/%d %H:%M:%S")
    client = re.search(r"client: ([^, ]+)", g["message"])
    return {
        "timestamp": time,
        "level": g["level"],
        "ip": client[1] if client else None,
    }
