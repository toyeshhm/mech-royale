# Mech Royale safe entry

A standard-library Python bot for the Builders Buildathon Mech Royale track (`mechfight/1`).
It heads for a point inside the next zone circle early, picks up weapons when it is calm,
leads gun shots, charges and releases the laser, lobs mortar shells by ballistics, uses
lightning inside its cone, strafes, backs off at low HP, and picks survival upgrades.

- `mech_bot.py`: the bot.
- `nav.py`: walkable grid, A* paths, line of sight and cover from the kit's map.
- `mechfight.py`: the kit's wrapper, provided code, unmodified.
- `tests/test_bot.py`: assert-based tests of the decision logic against the kit's recorded stream
  (`tests/data/stream.jsonl.gz`, also from the kit).
- Libraries: Python standard library only.
- No prior team code was reused.
- AI tools were used to write this bot.
- MIT licensed (`LICENSE`).

## Checks

```sh
make check                      # compile + unit tests
KIT=path/to/mechfight-kit       # the kit folder
cd $KIT
python3 check/check.py -- python3 -u <repo>/mech_bot.py
docker build --platform linux/amd64 -t mech-royale-entry <repo>
python3 check/check.py --map-dir /arena -- docker run --rm -i --platform linux/amd64 --network none \
  --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "$PWD/arena:/arena:ro" mech-royale-entry
```

The bot reads the kit's map (`nav.py`) for A* paths, line of sight and cover. If the map cannot
be read it steers straight and escapes by jumping and sidestepping.
