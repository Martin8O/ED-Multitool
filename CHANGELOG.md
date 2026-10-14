# Changelog

What changed in each version of ED Multitool. Newest first.

## 0.37.0 · 2026-10-15
- **First public release.** ED Multitool is now open to everyone on GitHub: download the zip from the
  release page; later versions announce themselves in EDMC's panel and on the page.
- **One station search on the Services tab.** Instead of a button per service, tick everything the station
  must have and press **Find nearest**: the services are grouped as Engineering (Material Trader, Technology
  Broker), Ship (Refuel, Repair, Restock, Outfitting, Shipyard), Trade and law (Interstellar Factors, Market,
  Black Market, Search and Rescue) and Data and on foot (Universal Cartographics, Vista Genomics, Pioneer
  Supplies). So you can ask for a Material Trader that also repairs and rearms, or Interstellar Factors with a
  Black Market. Nothing ticked finds any station you can land at.
- **A Material Trader of the kind you need** (raw, manufactured, encoded) and a **human or Guardian
  Technology Broker**: the 10 nearest of that kind, not just the nearest 20 of any kind.
- Every station in the list shows its faction and your standing there, and the fleet carrier, settlement and
  "will not let me dock" options apply to traders and brokers too.
- **Stations | Engineers** switch at the top of the card. After reopening the page, the boxes show what the
  list was searched for.
## 0.36.0 · 2026-10-15
- **Open source.** ED Multitool is free software under the MIT licence. Ideas and bug reports are welcome as
  GitHub issues; the About window links there.
- **The download now carries the page's pictures**: the emblem, the header picture and the window icon.
- **Clearer help**: the jump range (i) says which figures count your cargo, and "Jumps ≥" is counted "at your
  routing range".
- **Big numbers with commas** in the Powerplay answer line too ("holds 2,185 systems"), like everywhere else.

## 0.35.0 · 2026-10-14
- **Any station: pick the services it must have.** Tick Refuel, Repair, Restock, Market, Outfitting,
  Shipyard, Black Market, Universal Cartographics, Vista Genomics, Interstellar Factors, Material Trader,
  Technology Broker, Pioneer Supplies or Search and Rescue under "Must have" and only stations with every one
  of them are listed. Nothing ticked finds any station, as before. The page remembers what you ticked.

## 0.34.0 · 2026-10-14
- **Minor factions on the CMDR tab**: every faction that knows you, best first, with the game's word and
  number for your standing, the missions you completed for it, what it was going through when you last met it
  (war, boom, election…), where and when that was, and the nearest system you have seen it in. Factions you
  met but did nothing for are only counted.
- **Hostile warning**: arrive where a faction holds you Hostile and the EDMC panel (and the card) says so:
  its stations refuse you and its ships may attack.
- The Minor factions (i) explains why a station can show a better standing than the card: the game writes
  the number only when you arrive in a system or log in, not when you sell data or hand in a mission.
- Card headings start with a small orange triangle instead of a bar, which read like a capital I.

## 0.33.0 · 2026-10-14
- **Powerplay: three clear ways to ask** instead of "It holds / Where it is present": the systems a power
  **holds** (your merits reinforce), those **others hold** where it is present too (undermine), and the
  Unoccupied ones **it can acquire**. "Where it is present" used to mix all three, so far from the bubble it
  could list only Unoccupied systems held by nobody, which read as "my power is not there".
- **Clearer answer line**: "Li Yong-Rui holds 2 185 systems: the 10 nearest." instead of "10 000 or more", and
  when even the nearest is far away it says so ("None is closer than 1,267 ly"), so a long distance does not
  look like a wrong one.

## 0.32.0 · 2026-10-14
- **One look on every tab**: the same type sizes, buttons, fields, colours and spacing everywhere. Cards and
  buttons have small cut corners whose edges glow orange; every card heading starts with a short orange bar;
  the open tab is underlined in orange.
- **Colours that mean one thing**: the value each table is there for (price, profit, distance, reward…) is in
  the same bright blue on every tab; green means good or earned, amber take care, red danger. Data-age tags and
  the copy icons are quieter.
- **Day and night**: a light look alongside the dark one. The page follows the Windows setting; the sun / moon
  button at the top right switches, and this browser remembers it. The banner and the rank cards stay dark.
- **About**: the yellow (i) at the top right shows what the tool is, the version and what's new, where the data
  comes from, thanks, the source code and the author.
- **Clearer (i) explanations**: every one rewritten as a short lead, the terms one per line, the data source
  last. The (i) of Best trade, Services, Powerplay, Gear and Missions showed an empty bubble: fixed.
- **Clearer answers**: a search that found nothing now reads in quiet grey instead of green, and a card that
  has not been searched yet says what to do. Column names sit over their values; the faintest "$" on a
  route is easier to read; chart labels are larger. Distances show whole light years (a ship's jump range
  keeps its two decimals).

## 0.31.0 · 2026-10-14
- **"A new version is out"**: about once a day, and shortly after start, ED Multitool asks GitHub whether a
  newer version has been published. If so, a quiet strip appears above the page and one line in EDMC's panel,
  both linking to the release page for what's new and the download. Nothing is downloaded or installed for
  you, and nothing is shown while there is nothing newer, no release yet, or no internet. The check can be
  turned off in the page's footer or in EDMC's settings for ED Multitool.

## 0.30.0 · 2026-10-14
- **A new look at the top**: a space picture behind the title and the System / Ship / Jump range row, the
  title and an emblem in the middle; every tab has its own orange icon above its name, and the emblem is the
  window's icon in the taskbar and the browser tab.
- **Unofficial, said plainly**: the window's footer and the README say that ED Multitool is a fan-made tool,
  not affiliated with or endorsed by Frontier Developments.

## 0.29.0 · 2026-10-14
- **Deaths** (Stats tab): the game's statistics count no deaths, so a new card reads each one from your
  journals — how many, what coming back cost in total, and a line per death: when, who killed you (the pilot,
  their ship and combat rank; every pilot of a wing; or a station's or settlement's defences) and what it cost
  (the rebuy, free, or bankrupt).

## 0.28.0 · 2026-10-14
- **Sell cargo** (Trade tab, third button): what you carry and where it sells best. For each commodity in your
  hold, the three best-paying stations within reach that buy all of it (price, demand, distance, jumps, data
  age, your standing there), and one stop for the whole load: the best-paying station that buys everything, plus
  the one that pays more for part of it when there is one. Mission cargo, stolen goods and limpets are shown but
  not searched. It also shows the tonnes you mined this session and how many per hour.
- **Start EDMC before the game.** Started while the game runs, EDMC knows your hold only as tonnes per
  commodity until it next changes, so Sell cargo counts mission cargo and stolen goods as your own until then;
  the card says so while it lasts.

## 0.27.0 · 2026-10-14
- **Stats** (new tab, after CMDR): the game's own lifetime statistics, read from your journals — the same
  numbers sites like Inara show. Seven headline totals (wealth, systems visited, jumps, distance, exploration
  and exobiology sales, time played) with what they grew by in the last week and a small chart of each over
  your logins; profit by activity and spending by kind as bars; a card per activity with every figure that is
  not 0, and a few worked out from them (average jump, credits per hour played, average per sample sold).
  Figures the game adds later show up on their own.

## 0.26.0 · 2026-10-14
- **(i) on every card**: point at it (or tap it) for what the card shows, where its data comes from and how it
  is counted. The grey notes under the cards moved in there, so the page is quieter. A click keeps it open
  until a second click, Esc or a click elsewhere.
- **Ranks**: point anywhere on a rank's card for its whole ladder, from the lowest rank to the top: yours
  marked with the percent to the next, what moves you up, and what each rank opens (Sol, Achenar and the other
  permits, the Dropship, Corvette, Courier, Cutter…). No made-up thresholds: Frontier does not publish them.
- The Commander tab is now **CMDR**. Old links still open it.

## 0.25.1 · 2026-10-14
- **Gear**: every offered item says what it is right after its name: suit, kinetic pistol, laser rifle,
  plasma shotgun… ("unknown item" when ED Multitool does not know it).
- **Gear**: when jumps to a place are not counted, it says why: no position yet, crew in another ship, no ship
  or no FSD data known, Spansh failed, or only the 6 nearest systems are plotted. The note under the card
  says plainly how jumps are counted.

## 0.25.0 · 2026-10-14
- **Trade** (was Commodity): two buttons switch between **Best trade** and **Commodity**.
- **Best trade** (new): what to buy at a station and where to sell it for the most profit with your hold and
  credits (Spansh's trade router). It starts from the station you last docked at, or pick a system and then
  one of its stations with a market from a list. Both systems have a copy button, with the distance, the fewest
  jumps with the load aboard, the price, stock and demand, the profit per tonne and in all, and how fresh each
  market's data is.
- **Route** is the new name of the Search tab. Old links and the tab you had open still lead to the right place.

## 0.24.0 · 2026-10-13
- **Commander → Powerplay**: the rank is now the game's own. It stays 0 until you have done your power's
  introductory missions; before, it was worked out from your merits and could show a rank you do not have.
- **Every power by its systems**: all 12 powers, most systems held first, with yours highlighted (Spansh,
  asked when you open the tab, at most every 15 minutes).
- **Earnings → Balance over time**: point anywhere over the chart to see the time and balance of the nearest
  point.

## 0.23.0 · 2026-10-13
- **Missions** (new tab): your open missions from your journals — what each one is, who gave it, where it
  sends you (system and station, with a copy button), the reward and the time left, counting down and red
  under 2 hours. Distance in light years and the fewest jumps at your routing range; sort by time left or
  by distance. A finished job waiting to be handed in says so and where; it no longer runs out. Each login's
  list from the game sets it straight. The EDMC panel shows a new **Missions** line: how many, their reward
  and when the next one ends.
- **Engineers** moved onto the **Services** tab: an **Engineers** button next to Material Trader, Technology
  Broker, Interstellar Factors and Any station shows them in the same card (one tab fewer; old links to the
  Engineers tab open it).
- The tab row no longer pushes the page wider than a mid-size window: below 1200 px it gets smaller tabs and
  scrolls on its own.

## 0.22.0 · 2026-10-13
- **Earnings** (new tab): what you earned and spent this session, today and this week, read from your
  journals: exploration data, exobiology, trade profit, bounties and combat bonds, missions; repairs, fuel,
  ammo, ships and modules, fines, insurance. Credits per hour for the session.
- **Balance over time**: a chart of your credit balance since your first journal — exact at every login and
  followed through every credit in between, ending at your live balance.
- **Still carried, not sold**: the cartographic data you scanned since you last sold it (estimated with the
  first-discovery, first-mapped and probe bonuses, and what every body your discovery scan found adds) and the
  species you analysed since your last Vista Genomics sale, each with its value — all of it lost if you die
  first. The EDMC panel shows the totals on a new **Earned** line.

## 0.21.0 · 2026-10-13
- **Search → This system**: the moment you arrive, what the system is worth: bodies worth mapping and
  species worth sampling (counted with your route-scout minimums, leaving out what you already mapped or
  analysed), life nobody has sampled yet, and "first discovery possible" when no one has been there. Your own
  scans fill it in as you go, with values estimated the way Spansh estimates them. The EDMC panel shows it on
  a new **Here** line.
- The window can flash when one body in the system is worth more than a set amount (30M Cr to start with;
  switch and amount at the top right of the card).

## 0.20.0 · 2026-10-13
- **Commander → Ranks**: all eight ranks the game keeps (Combat, Explorer, Trade, CQC, Mercenary,
  Exobiologist, Federal and Imperial Navy) as cards in the game's colours, each with the percent to the next
  rank and an emblem of its own that grows with the rank: wings, crests, chevrons, a wreath at Elite and a star
  for each Elite I–V.

## 0.19.0 · 2026-10-13
- **Commander** tab: your reputation with the superpowers, your navy ranks, your Powerplay pledge with merits
  in all and this week and your rank, how many systems your power holds, and charts of how they moved over
  time, all from your journals.

## 0.18.0 · 2026-10-13
- **Services → Any station**: the 10 nearest places your ship can land at (pad size), nearest the arrival star
  first within a system, with your standing with each station's faction. Options: fleet carriers, surface
  settlements, hide stations that will not let you dock.

## 0.17.0 · 2026-10-13
- **Credits** next to your ship, in millions, live as you earn and spend.
- **Warnings**, only when there is one, next to your ship and above route scouting: wanted, hostile, warrant,
  illegal cargo or speeding in the system you are in; notoriety; unpaid fines and bounties, by faction (hover a
  warning for the details).

## 0.16.0 · 2026-10-13
- **New name: ED Multitool** (was ED Scout).
- Ready-to-install zip and a full install guide with pictures.
- A link can open a tab directly (`…/#commodity`, `#engineers`, `#gear` …).
- A reopened window shows the commodity you searched last next to its results.
- EDMC panel: the first row is now called "System".

## 0.15.0 · 2026-10-12
- Clear one-line messages when Spansh or Ardent is down, slow or busy; nothing gets stuck "searching".
- A maintenance page or a broken reply is never remembered as an answer.
- Crew in someone else's ship, taxis and walking on foot no longer change your own ship or fuel.
- "Game closed · last in …" when you quit the game; a warning strip when the journal folder cannot be read.

## 0.14.0 · 2026-10-12
- **Powerplay** tab: the nearest systems a power holds or is present in, their state and control, and what your
  merits do there.

## 0.13.0 · 2026-10-12
- **Engineers** tab: the nearest ship or on-foot engineer for a module and grade.
- **Services** tab: the nearest Material Trader, Technology Broker or Interstellar Factors.
- A copy icon after every system name.

## 0.12.0 · 2026-10-12
- **Commodity** tab: the nearest stations that sell or buy a commodity, with data age and your standing with
  each station's faction; stations that would not let you dock are left out.
- The window is split into tabs.

## 0.11.0 · 2026-10-12
- **Gear** tab: paste a post from the forum's pre-upgraded gear thread to see where it is, how far, and how good
  the offer is.

## 0.10.0 · 2026-10-12
- Route totals (scan, mapping, exobiology), presets (*ELW hunt*, *Bio rich*, *Quick trip*), "valuable planet
  types only", and recent destinations.

## 0.9.0 · 2026-10-12
- Bodies you have already mapped and species you have already analysed no longer count; a spent stop is swapped
  for a fresh one within the detour you allow.

## 0.8.0 · 2026-10-12
- The route follows you jump by jump; copy the next system with one click; `$`–`$$$$$` value ratings.

## 0.7.0 · 2026-10-12
- Your ship is always known (also after logging in on foot), and you can plan with another ship of your fleet.

## 0.6.0 · 2026-10-12
- "Route only": plain jumps, or with neutron star boosts.

## 0.5.0 · 2026-10-12
- **Route scouting**: the FSS/DSS and exobiology stops worth a detour between two systems, with your own minimum
  values.

## 0.2.0 – 0.4.0 · 2026-10-11/12
- The EDMC panel and settings, the big window, laden jump range from your journal, and the connection to Spansh.
