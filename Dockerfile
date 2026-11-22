FROM python:3.13-slim
WORKDIR /bot
COPY mechfight.py mech_bot.py ./
ENTRYPOINT ["python3", "-u", "/bot/mech_bot.py"]
