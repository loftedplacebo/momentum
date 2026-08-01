# Repository and VPS operations

## Repository

- GitHub: `https://github.com/loftedplacebo/momentum.git`
- Local hourly project: `C:\Momentum\binance\1hr`
- Local 15-minute research project: `C:\Momentum\binance\15m`

Create a clean local copy:

```powershell
git clone https://github.com/loftedplacebo/momentum.git C:\Momentum\new-copy
cd C:\Momentum\new-copy
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Update either existing local project:

```powershell
cd C:\Momentum\binance\1hr  # or C:\Momentum\binance\15m
git pull origin master
```

Publish source changes:

```powershell
git status
git add crypto_momentum README.md STRATEGY.md DEPLOYMENT.md requirements.txt strategy_v1.json
git commit -m "Describe the change"
git push origin master
```

Downloaded candles, optimiser outputs, logs and paper-account state are ignored
by Git. Never commit API keys, `.env` files, market data or `state/`.

## VPS paper-trading host

The VPS is reachable as `root@62.171.161.32`. The deployed project is
`/opt/momentum`; the service is `momentum-testnet.service`.

```bash
ssh root@62.171.161.32
cd /opt/momentum
systemctl status momentum-testnet.service
journalctl -u momentum-testnet.service -n 100 --no-pager
```

Deploy a source update after it is pushed:

```bash
cd /opt/momentum
git pull origin master
/opt/momentum/.venv/bin/pip install -r requirements.txt
systemctl restart momentum-testnet.service
systemctl status momentum-testnet.service
```

The service runs a no-order, stateful paper portfolio with a simulated $5,000
starting account. It uses Binance Futures testnet market data and refreshes the
liquid USDT perpetual universe daily (up to 100 markets). It does not submit
testnet or live orders.

## Safety checklist

1. Confirm the branch and `git status` before pulling or pushing.
2. Inspect service logs after every deployment.
3. Keep secrets only in VPS environment configuration, never in Git or shell
   history.
4. A green service proves only that the loop is alive; it does not prove
   profitability or order placement.
