# Super Mario Sunshine AP: logic review (pending checks)

**Status: nothing in this document is applied yet.** Every item below is a candidate waiting to be checked
and decided. Release `0.6.3-alelau18` ships with the logic of upstream `main` (plus the Pianta Village
ticket fix, #67), unchanged.

- Branch: [`alelau18-version`](https://github.com/Alelau18/archipelago-sms/tree/alelau18-version) on
  Alelau18/archipelago-sms. Code links point to the exact commit the review was done on.
- Region data on this branch is identical to Joshark/archipelago-sms `main` (20cc30b5), so every item
  also applies to upstream.

## How to read this

- **Tiers.** `normal` means no tricks. `hard`, `advanced` and `salty_tears` put tricks in logic on
  purpose, so a higher tier being looser than `normal` is intended. A tier that's set replaces the lower
  one completely (override with fallback). If `hard` leaves out a `shines=` or `location=` gate that
  `normal` has, that gate is gone at `hard`.
- **Direction.**
  - **Too loose:** logic can expect something the game doesn't allow, so seeds can become unwinnable.
    These matter most.
  - **Too strict:** logic only asks for more than needed. Seeds stay winnable; fixing it just widens
    logic.
- **Evidence** comes from:
  - The community logic sheet, compared against the repo in July 2026.
  - The Super Mario Wiki (episode-by-episode blue coin lists).
  - The dev's own unreleased logic commits on `Jake-NewClient`: 2773a453, d919bdeb, 8625177a,
    eebef1e9, 305cd155 and d7200652. They're referred to below as "dev WIP".
  - A generation sweep of 5,040 seeds that judges each seed twice: with the world's logic, and with a
    model closer to the real game.
  - Checks in the real game (Dolphin, US ISO).
- **Location IDs stay unchanged.** IDs are assigned in definition order, so moving a check to another
  episode region renumbers everything after it. That would break clients and trackers for seeds
  already being played. Fixes of the form "this coin only exists from episode N" are done as an episode
  gate (`location=` the previous episode's shine), not as a move.
- Known tricks aren't treated as bugs. When it's unclear whether a rule is a deliberate trick or a
  mistake, the item goes to "Needs a decision" (section 3).

---

## 1. Engine bugs (generation level)

These are in the rules code, not the region data. The sweep found them by filling seeds with the
world's logic and then checking them against the real-game model.

- [ ] **A1: Shine doors can open too early in vanilla access.** Pending: fix.
  - **What happens:**
    - Every `shines=N` requirement is capped at the Corona goal ([`sms_rules.py:93`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_rules.py#L93)).
    - Logic only counts progression shines. Since #72, extra shines are classified as filler
      ([`__init__.py:209`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/__init__.py#L209)).
    - The doors in the game count *every* received shine: Noki 20; Pinna and Gold Bird 10; Gelato,
      Sirena, Lily Pad, Yellow Goo and the Pianta hard-mode Yoshi route 5; Ricco, Boathouse and
      Pachinko 3.
    - So with a low goal, logic opens those doors before the game does. With `total_shines` under 20,
      Noki can never open.
  - **Measured** (seeds unbeatable in the real-game model; `total_shines` / `required_shines_percentage`):

    | Setting | Unbeatable |
    |---|---|
    | 10 / 100 | all of Noki in 100% of seeds |
    | 25 / 50 | 9% |
    | 40 / 20 | 3% |
    | 70 / 0 | 8% |
    | 70 / 72 (default) | 0% |
    | 150 / 90 | 0% |

    Ticket mode had 0 failures in 2,518 seeds.
  - **Proposed:** in vanilla access, make `max(goal, 20)` shines progression. Raise `total_shines` to
    20, with a warning, when it's lower. Cap doors at that count instead of at the goal. With this, the
    sweep had 0 unbeatable seeds.
  - This is likely what issue #65 ("Noki expected before available") runs into on current `main`. The
    0.6.2 empty-tier bug behind that issue is already fixed on `main`.

- [ ] **A2: `trade_shines_only` ignores every blue coin's requirements.** Pending: fix.
  - **What happens:**
    - In this mode the coins are events ([`regions.py:251`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/regions.py#L251)), and their requirements are never
      interpreted, so every coin is always "reachable".
    - 201 of the 240 coins that need a nozzle count as free, and the Boathouse trade shines only need
      the Boathouse.
  - **Measured** (unbeatable in the game): trade max 6 → 3%, **12 (default) → 10.7%**, 24 → 36%.
  - **Proposed:** collect the event coins and run their requirements through the same interpreter as
    real locations.

- [ ] **A3: Victory has no rule.** Pending: fix, and confirm the Rocket fact.
  - The final Bowser fight needs the Rocket Nozzle, to get above the tub and ground-pound it
    ([`regions.py:277`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/regions.py#L277)).
  - Only matters with minimal accessibility: 117 of 985 minimal seeds were unbeatable, mostly
    0%-goal configs.
  - **Proposed:** Victory requires Rocket, and Rocket isn't placed in Corona.

- [ ] **E10: Generation fails with `full_shuffle` and `trade_shine_maximum: 24`.** Pending: decide.
  - With 240 coins and 24 trades there's no slack, since shine 24 needs every coin. 2.7% of those
    seeds hit a FillError.
  - **Lean:** require some extra coins over 10 × trades, or lower the trade count.

- [ ] **N4: Rare FillErrors on fluddless starts.** Pending: low priority.
  - Sphere 0 is 3 locations plus one random early nozzle.
  - Failures were seen with fluddless + `all_shines_selectable` at 10/100 (7 of 150) and at 40/20
    (5 of 150). Fluddless with default settings: 0 of 150.
  - **Lean:** force a splasher as the early nozzle.

> Already fixed in `0.6.3-alelau18` (client side): with a 0% goal the client demanded 50 shines for
> Corona, so every such seed was unbeatable. The engine side of a 0% goal (doors dropped entirely) is
> part of A1.

---

## 2. Region data: logic can expect the impossible (recommended)

| ID | Location | Today | Proposed | Tiers | Evidence / source |
|---|---|---|---|---|---|
| A4 | Plaza, Pachinko Game ([`delfino_plaza.py:114`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L114)) | `normal` needs 3 shines. The `hard`/`advanced` override drops the gate, and `salty_tears` uses `manual_none=True, shines=3`, which throws it away. | Keep `shines=3` on every tier | hard, adv, tears | Data accident. Probe: `salty_tears`, no items, 0 shines → reachable. Audit + new. |
| A5 | Plaza, Burning Pianta blue ([`delfino_plaza.py:172`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L172)) | No shine gate | + 5 shines | all | The burning Pianta event starts at 5 shines. Dev WIP (305cd155) + audit. Its fluddless/ticket variants are E1. |
| A6 | Sand-shine blues. Pinna 1: Tree Sand Shine ([`pinna_park.py:27`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/pinna_park.py#L27)), Cannon Sand Shine ([`pinna_park.py:33`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/pinna_park.py#L33)). Gelato 1: Sand Cabana ([`gelato_beach.py:229`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/gelato_beach.py#L229)), Surf Cabana ([`gelato_beach.py:247`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/gelato_beach.py#L247)). Gelato 2: Big Sand Shine ([`gelato_beach.py:306`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/gelato_beach.py#L306)) | Yoshi alone works (Pinna), or a Yoshi + ep6 route (Gelato) | Drop the Yoshi-only routes | all | Dune buds react to water, not Yoshi juice. Dev WIP d919bdeb for Pinna. Gelato by the same reasoning; Gelato's own Middle Sand Shine already has no Yoshi route. |
| A7 | Pinna 1, Beach Butterfly A/B ([`pinna_park.py:332`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/pinna_park.py#L332)) | Yoshi alone | Yoshi + (Spray or Hover), gated on Pinna 5's shine | all | Hatching the egg needs fruit from sprayed sand (dev WIP). Sheet: butterflies exist eps 5–8; wiki: all 8. The gate is the safe side. Alternative: the dev's version without the gate. |
| A8 | Ricco 1, Tower Rocket blue ([`ricco_harbor.py:236`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/ricco_harbor.py#L236)) | Rocket from ep1 | + gate on Ricco 2's shine | normal, hard | Sheet: the coin exists eps 3–8. Its margin note ("1–8 with infinite wall kicks + hover") could be a `salty_tears` route; not added. |
| A9 | Ricco 4, Red Coins in Ricco Tower ([`ricco_harbor.py:321`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/ricco_harbor.py#L321)) | No requirement at all (the code has a TODO) | `normal`: Rocket or Hover. `hard`+: free | normal | The secret entrance is on top of the tower. Sheet `normal`: Hover. Dev WIP is equivalent. |
| A10 | Noki 3, Red Coins in a Bottle ([`noki_bay.py:170`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L170)). Noki 8, The Red Coin Fish ([`noki_bay.py:302`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L302)) | `salty_tears`: item-free | Any FLUDD nozzle | tears | Dev commit 8625177a: the AP build can't move Mario at all without FLUDD there. Dev WIP + audit. |
| A11 | Plaza, Shine Sprite in the Sand ([`delfino_plaza.py:14`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L14)) | `normal`: Spray or Hover | `normal`: (Spray + Rocket) or Hover. `hard`+ unchanged | normal | Sheet `normal`: Hover. Dev WIP + audit. |
| A12 | Noki 1, Bottom and Top Secret Path blues ([`noki_bay.py:48`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L48), [`noki_bay.py:54`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L54)) | In the episode 1 region, no gate | Gate on Noki 1's shine (kept in place for the IDs) | all | Sheet: eps 2, 4–8. Dev WIP moves them to ep2, which would renumber the IDs. |
| A13 | Noki 1, Underwater blue ([`noki_bay.py:120`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L120)) | No rule | Gate on "Noki Bay 4 - Eely-Mouth's Dentist". `salty_tears`: decide | normal, hard, adv | Wiki: the bay water is polluted and hurts Mario until ep4 is done. New finding. |
| A14 | Sirena 3, Box Hole ([`sirena_beach.py:144`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/sirena_beach.py#L144)) | `normal`/`hard`: Yoshi only | `normal`: Spray + Yoshi. `hard`: Yoshi + (Spray or Hover). `advanced`: free, as today | normal, hard | The ep3 egg wants a pineapple behind a route that needs spraying. The repo's own ep3 shine asks for S/H with Yoshi. Same class as issue #38. New. |
| A15 | Pianta 8, Soak the Sun ([`pianta_village.py:432`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/pianta_village.py#L432)) | No episode gate | Gate on "Pianta Village 8 - Fluff Festival Coin Hunt" | hard only | Wiki: done by replaying Fluff Festival, and the sun image isn't there before. Only `hard` is loose (Spray + Turbo passes it, but Fluff needs Rocket/Hover). **Check in game** whether a first ep8 play shows the image. New. |
| A16 | Pinna and Noki entrances ([`pinna_park.py:3`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/pinna_park.py#L3), [`noki_bay.py:3`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L3)) | Shines only | + Gelato 1's shine | vanilla access | Plaza unlock order. Dev commit eebef1e9; sheet notes say "post Gelato unlock". |
| A17 | Corona Mountain traversal ([`corona_mountain.py:3`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/corona_mountain.py#L3)) | No nozzle requirement | `normal`/`hard`: Spray + Hover. `advanced`: see E5. `salty_tears`: free | normal, hard | The lava boat section. The code's own Corona item-block comment says Corona needs both nozzles, yet a fluddless start can put the goal in logic with no FLUDD. Audit. |
| A18 | Ricco 8, Yoshi's Fruit Adventure ([`ricco_harbor.py:387`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/ricco_harbor.py#L387)) | `advanced`: Yoshi, or Rocket alone | Yoshi, or Rocket + Spray | adv, tears | Sheet: "Rocket + spray (for rocket storage)". New. |

---

## 3. Region data: too strict (recommended; these only widen logic)

| ID | Location | Today | Proposed | Tiers | Evidence |
|---|---|---|---|---|---|
| B1 | Pinna 4, The Wilted Sunflowers ([`pinna_park.py:428`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/pinna_park.py#L428)) | `hard`+: **Turbo only** (`normal` is Spray/Hover) | Spray, Hover or Turbo | hard+ | Pinna 5–8 gate on this shine, so today at `hard`+ all of Pinna 5–8 needs Turbo. Sheet `hard`: "Turbo?". Audit. |
| B2 | Routes a higher tier dropped by accident: Turbo Track ([`delfino_plaza.py:87`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L87)); Red Coin Field ([`delfino_plaza.py:93`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L93)); Ricco High Platform M ([`ricco_harbor.py:402`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/ricco_harbor.py#L402)); Ricco Far Ledge ([`ricco_harbor.py:136`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/ricco_harbor.py#L136)); Pianta 1 Waterfall ([`pianta_village.py:64`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/pianta_village.py#L64)) | Turbo Track `advanced` loses Turbo. Red Coin Field `salty_tears` is Hover only. High Platform M `hard` loses Yoshi. Far Ledge `salty_tears` loses the Yoshi + ep8 route. Waterfall `advanced` loses Yoshi | Put the lost routes back | various | A lower tier accepts an inventory the higher tier rejects. Sheet `salty_tears` for Red Coin Field: H, H+S, R+S, S+T, S+Y, T. |
| B3 | Bianco Yoshi routes gated on "Bianco Hills 8 - The Red Coins of the Lake" (about 20 coins + 100 Coins, e.g. [`bianco_hills.py:28`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L28)) | That shine needs Rocket or Hover, so each route is really Yoshi + (R or H) | Gate on "Bianco Hills 7 - Shadow Mario on the Loose", which unlocks ep8 | normal | Every Bianco coin exists in ep8 (wiki). |
| B4 | Gelato 1, Juicer ([`gelato_beach.py:91`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/gelato_beach.py#L91)) | Spray/Hover, or Sand Sprint done | Free | all | Sheet: free at every tier. Any fruit dropped in the juicer works. |
| B5 | Dev WIP trick additions: Bianco entrance ([`bianco_hills.py:3`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L3)); Gelato and Ricco entrances ([`gelato_beach.py:4`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/gelato_beach.py#L4), [`ricco_harbor.py:3`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/ricco_harbor.py#L3)); Noki 1 100 Coins ([`noki_bay.py:34`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L34)) | Bianco: Spray or Hover. Gelato/Ricco: Spray or Hover. Noki 100 Coins `advanced`: Spray or Hover | Bianco: + Yoshi, + Turbo at `advanced`. Gelato/Ricco: + Turbo at `advanced`/`salty_tears`. Noki 100 Coins: + free once Noki 5's shine is done | adv, tears | Dev's intended tricks. Their Noki line used `manual_none` with a `location`, which silently drops the gate; ported as the gate plus the Spray/Hover route instead. |

---

## 4. Needs a decision or an in-game check

| ID | Question | Details | Lean |
|---|---|---|---|
| E1 | **Fluddless start without tickets: which plaza state does it really load?** | `skip_forward` rule variants apply to fluddless starts too ([`sms_rules.py:54`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_rules.py#L54)), but the client only forces the late plaza (episode 8) in ticket mode ([`SMSClient.py:510`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/SMSClient.py#L510)). Those variants have no shine gates (Gold Bird 10, Lily Pad 5, Pachinko 3, Yellow Goo 5, Sirena 5) and assume a plaza Yoshi egg. Probe: fluddless, 0 shines → Gold Bird, Lily Pad, Pachinko and Police Yellow Goo in logic. Both audits found this independently. | **Check in game.** If the plaza is early, limit `skip_forward` to ticket mode, or give fluddless the shine gates. |
| E2 | **Bianco 1 bits 172/188 swapped?** Towers House ([`bianco_hills.py:176`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L176)), Towers House M ([`bianco_hills.py:109`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L109)) | The sheet labels the two bits the other way round, and each side's rules match its own labels. If the sheet is right, the graffiti coin is free at `hard`+, and the tower coin is in logic with Spray alone at `normal`. | **Check in game:** in Bianco ep1, spray the M on the Pianta lady's house and see which check is sent. |
| E3 | Plaza Jail Cell ([`delfino_plaza.py:203`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L203)) | Dev WIP frees it at `normal`. Sheet and `main` have Hover at `normal`. | Keep Hover |
| E4 | Lily Pad Ride at `salty_tears` ([`delfino_plaza.py:84`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L84)) | Item-free today. The island pipe is under goop only Yoshi juice removes; the community tears method is a Spray banana clip. | Spray |
| E5 | Corona at `advanced` (see A17) | Spray *or* Hover (hoverless with precise spin dives) | Yes |
| E6 | Sirena entrance, Hover alone ([`sirena_beach.py:4`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/sirena_beach.py#L4)) | `main` and dev WIP allow it at `advanced`. The audit wanted the pineapple clip at `salty_tears` only. | Keep it at `advanced` (dev's call) |
| E7 | Ricco 8 Fish Basket ([`ricco_harbor.py:412`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/ricco_harbor.py#L412)) | Dev WIP moves it to episode 6. The sheet (and `main`) say episode 8 only. Moving it would also renumber the IDs. | Keep ep8 |
| E8 | Noki 1 Rocket blue ([`noki_bay.py:60`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L60)) | Dev WIP tiers: `advanced` + Spray+Turbo; `salty_tears` Rocket, Spray+Turbo or Hover. That drops Turbo alone at `salty_tears`. | Skip unless someone knows |
| E11 | Pianta Village entrance, `hard` | Sheet: Yoshi + Hover. `main`: Yoshi + 5 shines. | Decide |

### Could be a trick, or could be a mistake (all left as-is unless someone knows)

| Location | Today | Why it's in question |
|---|---|---|
| Ricco 8 Wall Klamber ([`ricco_harbor.py:399`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/ricco_harbor.py#L399)) | Free at `hard` | The wiki's non-Yoshi method needs water. In ticket mode Ricco has no nozzle requirement, so it's truly item-free. The sheet agrees with the repo. |
| Plaza Lighthouse Roof ([`delfino_plaza.py:36`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L36)) | Free at `salty_tears` | The sheet cell says "See note:", and the note didn't survive the export. |
| Gelato 6 Yellow Goo Dune Bud ([`gelato_beach.py:429`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/gelato_beach.py#L429)) | Yoshi only | Wiki: Yoshi clears the goop, then you spray the bud. That may need Yoshi + Spray/Hover. The sheet says Yoshi only. |
| Noki 1 Spawn / Coast blues ([`noki_bay.py:112`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L112), [`noki_bay.py:118`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L118)) | Free from ep1 at `advanced` | They float over water that's poison in eps 1–2. Fine if a jump from the start platform reaches them. |
| Noki 1 A Golden Bird ([`noki_bay.py:27`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/noki_bay.py#L27)) | Spray, in the episode 1 region | Wiki: it's "accessed by replaying any other normal mission". |
| Pinna 1 Sand M ([`pinna_park.py:69`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/pinna_park.py#L69)), Pinna 6 Park Butterfly ([`pinna_park.py:473`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/pinna_park.py#L473)) | Yoshi routes | Both are by the pool outside the park. Only the beach Yoshi could reach them, and its fruit needs sprayed sand. |
| Sirena 3 Attic, Sirena 4 Attic Boo ([`sirena_beach.py:166`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/sirena_beach.py#L166), [`sirena_beach.py:200`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/sirena_beach.py#L200)) | No rule | IGN: Giant Boos block the attic in ep3, and Boos need spraying. The sheet marks both free. |
| Pianta 1 Moon ([`pianta_village.py:188`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/pianta_village.py#L188)) | Yoshi route at `hard`+ | You spray the moon from the golden mushroom; getting Yoshi up there is doubtful. The sheet has no Yoshi. |
| Plaza Yoshi routes in general, e.g. Chuckster ([`delfino_plaza.py:148`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L148)), East Bell ([`delfino_plaza.py:46`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L46)), Shine Gate ([`delfino_plaza.py:59`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L59)) | Some Yoshi routes have a 5-shine gate, others don't | It depends on whether the plaza egg (the AP Yoshi item makes it spawn) exists in every plaza state. |
| Red Coin Field, Turbo alone at `salty_tears` ([`delfino_plaza.py:93`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/delfino_plaza.py#L93)) | Not in logic | The sheet lists it; the code comment says "may be possible". |
| East Bell / Shine Gate, Spray+Turbo at `advanced`+ | Missing from the ticket/fluddless variants | Those modes lose the route. |

---

## 5. Other findings (client / patch)

- [ ] **N1: Corona's stage ID is wrong in the ticket table.** Pending: fix.
  - The client lists Corona with `course_id=34` ([`SMSClient.py:803`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/SMSClient.py#L803)).
  - Checked in game by redirecting a stage load: stage 34 (0x22) is the intro movie on Peach's plane,
    and **Corona Mountain is stage 52 (0x34)**.
  - So in ticket mode the "no ticket" boot-out never fires for Corona.
  - **Proposed:** 52. Also check whether Corona's entrance can be reached before the goal at all.
- [ ] **N2: Ticket mode with a 0% goal.** Pending: check in game.
  - Since `0.6.3-alelau18`, a goal of 0 opens Corona right away. Before, 0 was read as 50, so Corona
    never opened in those seeds.
  - That also stops the client forcing plaza episode 8 in ticket mode from the first tick
    ([`SMSClient.py:510`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/SMSClient.py#L510)).
  - If some level entrances only exist in the late plaza, they could be missing in that setup.
- [ ] **N3: Issue #69, Noki enterable in ticket mode without its ticket.** Pending: needs a repro.
  - The logic is right: the entrance requires the ticket.
  - In ticket mode the patch sets Noki's shine door to 0, so the only guard is the client's boot-out.
  - That only fires if a 0.2 s poll catches the stage change.
- [ ] **N5: Gelato 5 Rocket Box** ([`gelato_beach.py:413`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/gelato_beach.py#L413)). Pending: cleanup.
  - Its "any nozzle + Sand Sprint" route means Yoshi alone in ep5, but the Gelato egg is ep6 only.
  - Harmless today.
- [ ] **N6: Bianco 6 Underwater Left** ([`bianco_hills.py:505`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L505)). Pending: cleanup.
  - By elimination it's the lake coin that only exists in eps 7–8; the lake is poison in ep6.
  - No practical effect, because reaching ep6 means ep7 is reachable.

---

## 6. Recommended to skip

- Seven Bianco relaxations the dev made and then discarded in his own merge (c927df36). The sheet
  contradicts most of them:
  - Sail Platform + Rocket ([`bianco_hills.py:234`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L234))
  - Treetop + Rocket ([`bianco_hills.py:298`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L298))
  - Tourist with Turbo from `hard` ([`bianco_hills.py:316`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L316))
  - Hillside Pokey + Rocket ([`bianco_hills.py:390`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L390))
  - Blue Bird + Yoshi at ep8 ([`bianco_hills.py:507`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L507))
  - Turbo Box free at `hard` ([`bianco_hills.py:516`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L516))
  - Towers House O + Turbo at `advanced` ([`bianco_hills.py:542`](https://github.com/Alelau18/archipelago-sms/blob/eef9a3c4ab82f3b2dc2ce41c377e1d0e862275e8/worlds/sms/sms_regions/bianco_hills.py#L542))
- Removing Sirena's `advanced` Hover route (see E6).
- Rewriting the Bianco 1 100 Coins `normal` tier. The duplicated requirement there is already removed
  in this release.

---

## Appendix: how this was checked

- **Reachability probe.** Every nozzle combination × shine counts (0–999) × 64 option sets (4 tiers ×
  vanilla/tickets × spray/hover/fluddless, plus `trade_shines_only`, `all_shines_selectable` and small
  shine pools). It shows exactly which checks each change moves in or out of logic, and where.
- **Generation sweep.** 4 tiers × 2 access modes × 3 starting nozzles × 3 blue coin modes × 2
  `all_shines_selectable` × 7 shine settings × 5 seeds = 5,040 seeds. Coin shines, nozzle boxes, trade
  count and accessibility were randomised per cell. Each seed is judged with the world's logic and
  with a real-game model: all received shines count, doors aren't capped, Corona opens at the client's
  goal, blue coin events keep their requirements, and Victory needs Rocket.

  | Run | OK | Unbeatable in game | Checks lost | FillError |
  |---|---|---|---|---|
  | branch as released | 3686 | 1044 (718 of them at 0 shines / 0%) | 305 | 5 (all fluddless) |
  | + A1–A3 + the 0%-goal client fix | 5031 | 0 | 0 | 9 (all fluddless) |

  The world's own logic never produced an unbeatable seed. Every failure is logic disagreeing with the
  game.
- **In the real game.** Dolphin, US ISO. So far used for the DeathLink work and for N1. E1, E2, A15 and
  N2 still need the in-game checks listed above.
- Ready-made patches exist for most items (the audit port, and the engine fixes for A1–A3). Ask
  Alelau18 for them.
