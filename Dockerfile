FROM python:3.13-slim
WORKDIR /bot
COPY mechfight.py nav.py dodge.py loot.py clearline.py mech_bot.py ./
ENTRYPOINT ["python3", "-u", "/bot/mech_bot.py"]
