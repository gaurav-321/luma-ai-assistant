# Docker Build and Deploy

## 1. Prepare env file

```powershell
Copy-Item .env.example .env
```

Update `.env` with your real values (especially `TELEGRAM_BOT_TOKEN`).

## 2. Build image

```powershell
docker compose build
```

## 3. Start service

```powershell
docker compose up -d
```

## 4. Check status

```powershell
docker compose ps
docker compose logs -f crew-personal-agents
```

## 5. Access web UI

- URL: `http://localhost:8787`
- Health: `http://localhost:8787/health`

## 6. Deploy updates

```powershell
docker compose down
docker compose build --no-cache
docker compose up -d
```

## 7. Stop

```powershell
docker compose down
```
