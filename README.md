# Inventory and Reorder Alerts

**Language:** Python (FastAPI) &nbsp;|&nbsp; **Needs:** Postgres + Redis

This is a **starter**. The application already works. Your job is everything
that gets it building, tested and running in CI.

---

## You do not need Python installed

You will build this into a container, and the container brings its own
Python 3.12. You are not being asked to extend the app — you are being asked
to ship it.

---

## 1. What this app needs

| | |
|---|---|
| **Runtime** | Python 3.12 |
| **Install dependencies** | `pip install -r requirements.txt` |
| **Start the app** | `uvicorn app.main:app --host 0.0.0.0 --port 8080` |
| **Listens on** | port 8080, bound to `0.0.0.0` |
| **Environment variables** | `DATABASE_URL`, `REDIS_URL` |
| **Needs running first** | Postgres, Redis, and the migrations applied |

### What it does

Stock tracked across several warehouses. Every sale, delivery, write-off and inter-warehouse transfer is a movement. A checker works out the average daily use of each line, turns that into a reorder point using the supplier's lead time, and raises an alert with a suggested order quantity when the shelf drops below it.

### Endpoints

```
GET  /health
GET  /warehouses                    every warehouse and what it holds
GET  /stock?warehouse=MUM&sku=KB-104  live position + reorder point per line
POST /movements   {"sku":"CB-001","warehouse":"MUM","kind":"sale","qty":20}
POST /transfers   {"sku":"MN-270","from":"MUM","to":"BLR","qty":3}
POST /check                         run the low-stock checker
GET  /alerts?limit=20               what it has raised
```

`/health` reports Postgres and Redis **separately**. If it says
`postgres: false` the app started fine and your compose wiring is wrong —
do not go looking in the application code.

### Migrations

`migrations/` holds `.sql` files applied **in filename order** before the app
starts. They create the tables and insert sample data. A container running
`psql` over them in order is enough; you do not need a migration tool.

---

## 2. What you must write

| File | What it has to do |
|---|---|
| `Dockerfile` | Install dependencies **before** copying source, pin the base image, do not run as root. |
| `docker-compose.yml` | App + Postgres + Redis + a migration step, one `docker compose up`. |
| `.circleci/config.yml` | lint → unit tests → integration tests → secret scan → image build |
| Unit tests | For `app/reorder.py`. No database, no network. |
| Integration tests | Against a real Postgres and Redis as CircleCI service containers. |

Then push your image to **your own Docker Hub account**, tagged `:1.0`.

### When it works

```bash
docker compose up --build
curl localhost:8080/health
```

```json
{"status":"ok","postgres":true,"redis":true}
```

---

## Where the marks are

`app/reorder.py` is **pure logic** — plain functions over plain data, no
database and no HTTP. Start your tests there. Use pytest:
`pytest --cov=app --cov-report=term-missing`. Minimum 70%.

Start with `classify_movement`. Feed it every combination of kind, sign and transfer id you can think of, including a mislabelled row. Then do the window boundaries in `consumption_in_window` - a movement exactly `window_days` old is out, one day newer is in.

## Why Redis is here

Alert de-duplication. The checker is meant to run on a schedule. Without Redis the same sad item raises the same alert every single time it runs - every five minutes, all night, until somebody mutes the channel and misses the one that mattered. The key is set with `NX` and a TTL, so only the first checker to notice wins, and it is deleted the moment the line recovers so a genuine second dip is still heard.

## The hard part

**Stock moving between warehouses must not look like consumption.** A transfer leaves one warehouse exactly the way a sale does and arrives in another exactly the way a delivery does, but the company owns the same number of units it did a minute ago. Get this wrong and the branch you emptied starts forecasting demand it never had and reorders stock that is already sitting in the other warehouse. Decide how you recognise a transfer leg, and say why you trust that signal over the movement's own label.

Write your answer in your README. It is worth more marks than the feature.

---

## Getting unstuck

| Symptom | Almost always |
|---|---|
| `/health` says `postgres: false` | Wrong hostname. In compose the host is the **service name**, not `localhost`. |
| Page will not load, logs fine | No `ports:` mapping, or bound to `127.0.0.1` not `0.0.0.0`. |
| `relation "..." does not exist` | Migrations did not run, or the app started before they finished. |
| Build takes minutes each time | `COPY . .` is above your dependency install. |
| CI cannot reach the database | In CircleCI service containers the host **is** `localhost` — opposite of compose. |
