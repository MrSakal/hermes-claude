# Hermes Claude Code plugin — felülvizsgálat és tervezet

> Cél: olyan **Hermes model‑provider plugin**, amit csak telepíteni kell, és
> ezután a Claude Code (Claude CLI / `claude-agent-sdk`) ugyanúgy megjelenik a
> Hermes modellválasztóban és ugyanúgy viselkedik, mint egy natív provider
> (OpenAI / Google / Anthropic). Ez a dokumentum a jelenlegi állapot
> felülvizsgálata + prioritizált tervezet.

Állapot: a repó egy **félig kész, működőképes irányba mutató** prototípus.
Az ötlet (lokális OpenAI‑kompatibilis proxy + bridge a Claude Code felé) helyes
és van rá precedens a Hermesen belül is (`copilot-acp`). A baj az, hogy a plugin
**nem a Hermes által ténylegesen használt felderítési/regisztrációs úton**
illeszkedik be, és emiatt törékeny kerülőutakat (monkeypatch, kézi
registry‑injektálás) használ. A javítások nagy része **egyszerűsítés**, nem
új funkció.

---

## 1. Mit csinál most a plugin (architektúra)

```
Hermes model picker ──▶ ProviderProfile "hermes-claude-code"
                          base_url = http://127.0.0.1:35345/v1
                          ▼
                   lokális FastAPI proxy  (/v1/models, /v1/chat/completions, /health)
                          ▼
                   ClaudeBridge ──▶ claude_agent_sdk.query(...)
                          └─fallback─▶ `claude -p --output-format json`
```

Fő elemek:
- `provider.py` — `ProviderProfile` regisztráció + **kézi `PROVIDER_REGISTRY`
  injektálás** + runtime monkeypatch behúzása.
- `runtime.py` — **monkeypatcheli** a `hermes_cli.runtime_provider.resolve_runtime_provider`‑t,
  hogy az `external_process` providernek ne üres `api_key`‑t adjon.
- `proxy.py` — lokális proxy + életciklus (start/stop/status/health), subprocess autostart.
- `bridge.py` — OpenAI ⇄ Claude Code fordítás, strict/agentic mód, tool‑call
  visszafordítás, vízió, reasoning, streaming, CLI fallback + **magyar nyelvű
  regex heurisztikák** (pl. URL → `web_extract`, „hermes mappa" → `search_files`).
- `mcp_server.py` — a Hermes toolokat in‑process SDK MCP szerverként adja a
  Claude Code‑nak, de strict módban a tool‑use‑t visszafordítja OpenAI
  `tool_calls`‑ra (Hermes marad a végrehajtó).
- `plugin.py` — `register(ctx)`: provider regisztráció + `ctx.register_hook`/
  `register_cli_command`/`register_command` (session‑start autostart, `hermes
  claude-code …`, `/claude-code`).
- `plugin.yaml` — `kind: standalone`, root szinten.
- `pyproject.toml` — `[project.entry-points."hermes_agent.plugins"]
  hermes-claude-code = "hermes_claude_code.plugin"`.

A teszt‑lefedettség jó (proxy életciklus, stream/non‑stream, vízió, tool replay,
hibakezelés, doctor).

---

## 2. Hogyan néz ki egy valódi Hermes model‑provider plugin

Forrás: a hivatalos Hermes Agent fejlesztői dokumentáció és a forráskód
(`NousResearch/hermes-agent`). A lényeg:

**Felderítés (provider discovery) — `providers/__init__.py` → `_discover_providers()`**
lusta (első `get_provider_profile()` / `list_providers()` hívásra fut) és
**kizárólag könyvtár‑alapú**:

1. Beépített: `<repo>/plugins/model-providers/<name>/`
2. Felhasználói: `$HERMES_HOME/plugins/model-providers/<name>/`
3. Legacy: egyfájlos `providers/<name>.py`

Minden plugin‑könyvtár importálódik, és a modul‑szintű `register_provider(profile)`
hívás regisztrálja magát. **A felhasználói plugin felülírja az azonos nevű
beépítettet (last‑writer‑wins).**

**Kötelező szerkezet:**
```
plugins/model-providers/<name>/
├── __init__.py    # KÖTELEZŐ — import közben hívja: register_provider(profile)
├── plugin.yaml    # ajánlott: name, kind: model-provider, version, description, author
└── README.md      # opcionális
```

**Minimál `__init__.py`:**
```python
from providers import register_provider
from providers.base import ProviderProfile

register_provider(ProviderProfile(
    name="acme-inference",
    aliases=("acme",),
    display_name="Acme Inference",
    api_mode="chat_completions",       # vagy anthropic_messages / codex_responses / bedrock_converse
    env_vars=("ACME_API_KEY", "ACME_BASE_URL"),
    base_url="https://api.acme.example.com/v1",
    auth_type="api_key",
    default_aux_model="acme-small-fast",
    fallback_models=("acme-large-v3", "acme-small-fast"),
))
```

**Mit drótoz be automatikusan egy `api_key` típusú provider (a dokumentáció szerint):**
- `auth.py` `PROVIDER_REGISTRY` bejegyzés (kredenciál‑feloldás env‑var alapján),
- `api_mode=chat_completions`, CLI `--provider` flag, `hermes model` menü,
  `provider:model` alias szintaxis, runtime resolver konfiguráció.

**Fontos megszorítás (a forráskódból igazolva):**
- A `PROVIDER_REGISTRY` auto‑extend **csak `auth_type == "api_key"`** providereket
  importál, amiknek van env‑varjuk. Az `external_process`/speciális (copilot/kimi/zai)
  esetek ki vannak hagyva.
- A `resolve_runtime_provider()` az `external_process`‑t **csak névről**, kizárólag
  a `copilot-acp` esetre kezeli — nincs általános ág. Minden más provider az
  általános úton megy, ami `api_key`‑t vár.

> Következtetés: az `external_process` út **nincs kész** harmadik feleknek; a
> jelenlegi plugin pont ezért kényszerül monkeypatchre és kézi
> registry‑injektálásra. Ez a fő strukturális hiba forrása.

**`ProviderProfile` mezők (valódi `providers/base.py`):**
`name`, `api_mode="chat_completions"`, `aliases=()`, `display_name`, `description`,
`signup_url`, `env_vars=()`, `base_url`, `models_url`, `auth_type="api_key"`,
`supports_health_check=True`, `supports_vision=False`,
`supports_vision_tool_messages=True`, `fallback_models=()`, `hostname`,
`default_headers`, `fixed_temperature`, `default_max_tokens`, `default_aux_model`.
Metódusok: `get_hostname`, `prepare_messages`, `build_extra_body`,
`build_api_kwargs_extras`, `get_max_tokens`, `fetch_models`.

---

## 3. Eltérések / problémák (a repó vs. a valódi Hermes)

| # | Súly | Terület | Jelenlegi állapot | Probléma |
|---|------|---------|-------------------|----------|
| 1 | **P0** | Mappaszerkezet | `src/hermes_claude_code/` + root `plugin.yaml` | A `_discover_providers()` **csak** `plugins/model-providers/<name>/`‑t néz. Így a plugin felderítése **nem garantált**. |
| 2 | **P0** | `plugin.yaml` `kind` | `kind: standalone` | A model‑provider manifest `kind: model-provider`. A `standalone` nem ismert provider‑kind. |
| 3 | **P0** | Terjesztési mechanizmus | pip entry point `hermes_agent.plugins`, modulra mutatva (`...plugin`, nem `:register`) | A provider‑felderítés **nem olvas** `hermes_agent.plugins` entry pointot (könyvtár‑alapú). A doc példája is `csomag:register` függvényre mutat, nem modulra. |
| 4 | **P0** | `auth_type` | `external_process` | Sem az `auth.py` auto‑extend, sem a `resolve_runtime_provider` nem kezeli általánosan → emiatt kell a monkeypatch + kézi registry‑injektálás. |
| 5 | **P1** | `register(ctx)` szignatúra | ctx‑et **kötelezően** vár | A doc szerinti betöltés `register()`‑et hív (vagy import‑szintű regisztráció). Ha ctx nélkül hívják → `TypeError`, a plugin csendben elbukik. |
| 6 | **P1** | `ctx.register_hook` / `register_cli_command` / `register_command` | használatban (session autostart, CLI, slash) | Ezek a **„general plugin" ctx‑API**‑hoz tartoznak, nincs bizonyíték, hogy a model‑provider felderítési út átadja a ctx‑et. A proxy autostart így nem futna le. |
| 7 | **P1** | `default_aux_model` | nincs beállítva | A kiegészítő feladatok (vízió‑összegzés, kompresszió, memória) vagy elbuknak, vagy a drága fő modellt használják. |
| 8 | **P1** | Modellkatalógus | display nevek (`"Opus 4.8"`) a katalógusban, `MODEL_ID_ALIASES` csak a proxyban old fel | A Hermes oldali katalógus/aux/`provider:model` az **id**‑t várja (`claude-opus-4-8`). Display nevet a `display_name`/label adja, nem a model‑id. |
| 9 | **P2** | strict‑mód heurisztikák (`bridge.py`) | magyar nyelvű regex (URL→`web_extract`, „hermes mappa"→`search_files`, „permission" chatter) | Túlillesztett, törékeny, rejtett hibákat okozhat. Nem skálázódik nyelvre/esetre. |
| 10 | **P2** | `ProviderProfile` shim | hiányzik `supports_vision_tool_messages`, `fixed_temperature`, `get_hostname` | Csak standalone/teszt módban számít, de érdemes szinkronban tartani. |
| 11 | **P2** | `tool_choice` | nem érvényesül a bridge‑ben | A kért `tool_choice` (`required`/`none`/adott függvény) figyelmen kívül marad. |

---

## 4. Prior art (csinálta‑e már valaki, és hogyan)

- A nyilvános ökoszisztémában **szinte minden megoldás a fordított irányba megy**:
  Claude Code *fogyaszt* más modelleket egy proxyn keresztül
  (`ANTHROPIC_BASE_URL` → proxy → OpenAI/OpenRouter/Ollama). Példák:
  `1rgs/claude-code-proxy`, `fuergaosi233/claude-code-proxy`,
  `nielspeter/claude-code-proxy`, `musistudio/claude-code-router`, OpenRouter
  integráció. Ezek mind az Anthropic Messages API‑t emulálják *kifelé*.
- Amit **ez a repó** csinál — a Claude Code‑ot *providerként* (modellforrásként)
  beadni egy másik agentnek (Hermes) —, az ritkább. A legközelebbi minta maga a
  Hermes **`copilot-acp`** providere: külső folyamat (GitHub Copilot ACP) lokális
  hídként, providerként megjelenítve. Ez igazolja, hogy a „lokális híd =
  provider" minta legitim a Hermesben.
- Tanulság: a lokális OpenAI‑kompatibilis proxy megközelítés **jó**, csak a
  Hermesbe való **beillesztés módját** kell a dokumentált `api_key` +
  könyvtár‑plugin útra hozni, a monkeypatch helyett.

Források a chat‑összefoglalóban (Hermes docs + GitHub) szerepelnek.

---

## 5. Lefedi‑e a Hermes agent viselkedését?

Nagyrészt **igen**, de van hézag:

| Képesség | Állapot | Megjegyzés |
|----------|---------|------------|
| Chat completions (stream + non‑stream) | ✅ | SSE chunkok, finish_reason rendben. |
| Tool calling (Hermes a végrehajtó) | ✅ (strict mód) | MCP‑n át kapja, OpenAI `tool_calls`‑ra fordít vissza. Jó megközelítés. |
| `tool_choice` érvényesítés | ❌ | Nincs kezelve (lásd #11). |
| Vízió (image_url / base64) | ✅ | SDK streaming‑input blokkok, `supports_vision=True`. |
| Reasoning / thinking | ✅ | `reasoning_content`, `effort` → SDK `thinking`. |
| Hibakezelés (401/402/429/…) | ✅ | API error → HTTP status leképezés, CLI fallback nem mossa el. |
| Aux modell (vízió‑összegzés, kompresszió) | ⚠️ | `default_aux_model` nincs → drágán/hibásan futhat (lásd #7). |
| Modellválasztóban megjelenés „mindenhol" | ⚠️ | Csak a megfelelő könyvtár‑plugin + `api_key` regisztrációval garantált (lásd #1–#4). |
| `provider:model` alias, CLI `--provider` | ⚠️ | Csak ha az auto‑extend felveszi (azaz `api_key` típus kell). |
| Context length / token‑budget | ❌ | `model_metadata` nincs megadva → kompresszió/küszöb pontatlan lehet. |

---

## 6. Tervezet — mit változtatni / javítani / erősíteni / optimalizálni

### P0 — Auth és számlázás: ELŐFIZETÉS (OAuth), NEM API kulcs  ⚠️ KIEMELT

A felhasználó **a Claude előfizetését** (Pro/Max, `claude login` OAuth) akarja
használni, **nem** API kulcsot, és a token‑alapú „extra usage" (túlhasználat)
**bekapcsolása nélkül**. Két különböző auth‑réteget kell szétválasztani:

- **Hermes → lokális proxy**: a `ProviderProfile` „api_key"‑e csak placeholder,
  a localhost proxy eldobja. Semmi köze a Claude számlázásához. (Lásd lent #1.)
- **Bridge → Anthropic**: itt számít az előfizetés.

**A JELENLEGI MEGOLDÁS MÁR JÓ (igazolva a kódból).** A repó **auth‑agnosztikus**:
- `bridge.py` a CLI‑t `create_subprocess_exec(*cmd, …)`‑szal indítja **`env=`
  nélkül** → örökli a környezetet; a parancs `claude -p …` (**nincs `--bare`,
  nincs `--api-key`**).
- A SDK‑út (`claude_agent_sdk.query`) sem ad át kulcsot — a háttérben a `claude`
  bináris hitelesítését (a `claude login` OAuth credential store) használja.
- `proxy.py` Popen szintén `env=` nélkül indul → örökli a környezetet.
- Sehol nincs `ANTHROPIC_API_KEY` beállítás; a `LOCAL_API_KEY` csak a Hermes→proxy
  placeholder, **soha nem** megy `ANTHROPIC_API_KEY`‑ként a backendbe.

→ Ezért működik **előfizetéssel, túlhasználat nélkül**: ha nincs
`ANTHROPIC_API_KEY` a környezetben és a felhasználó `claude login`‑nal (Pro/Max
OAuth) be van jelentkezve, a bridge a tárolt OAuth‑credentialt használja. **Ezen
nem kell változtatni.** (A korábbi „át kell állni API kulcsra" aggály téves volt;
az `auth_type="api_key"` javaslat lent KIZÁRÓLAG a Hermes→proxy belső réteg.)

**Csak hardening (nem hibajavítás):**
- **`doctor` logika megfordítása.** Most az `ANTHROPIC_API_KEY set`‑et tekinti
  „auth OK"‑nak (zöld) — ez előfizetéses esetben **félrevezető és veszélyes**: egy
  véletlenül beállított kulcs (`.bashrc`/`.env`/devcontainer) **csendben felülírja
  az előfizetést és API‑áron számláz**. A zöld jel a `claude login` OAuth megléte
  legyen; az API‑kulcs jelenléte **figyelmeztetés**.
- **Opcionális env‑higiénia:** egy `HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1` flag
  mögött a bridge a backend‑subprocess env‑jéből vegye ki az `ANTHROPIC_API_KEY`‑t,
  hogy akkor is az előfizetés menjen, ha a környezetben ott van egy kulcs.
- **`--bare` továbbra is kerülendő** (bare módban a `CLAUDE_CODE_OAUTH_TOKEN` nem
  olvasódik be) — a jelenlegi kód nem is használ `--bare`‑t, ez csak megőrzendő.
- **Headless/CI** esetre dokumentálható a `claude setup-token` →
  `CLAUDE_CODE_OAUTH_TOKEN` út, ami szintén öröklődik a subprocessbe.

### P0 — Strukturális illeszkedés (enélkül nem „natív" a beépülés)

1. **`auth_type: external_process` → `api_key`** (placeholder kulccsal — kizárólag
   a Hermes→proxy réteghez; lásd a fenti auth‑szekciót).
   - `env_vars=("HERMES_CLAUDE_CODE_API_KEY", "HERMES_CLAUDE_CODE_BASE_URL")`,
     `base_url = http://127.0.0.1:<port>/v1`.
   - A `__init__.py`/telepítő gondoskodik róla, hogy a `HERMES_CLAUDE_CODE_API_KEY`
     egy nem‑üres placeholder értéket kapjon (a lokális proxy nem ellenőrzi).
   - **Ezzel eldobható**: a teljes `runtime.py` monkeypatch ÉS a `provider.py`
     kézi `PROVIDER_REGISTRY` injektálás. Az `auth.py` auto‑extend és a
     `resolve_runtime_provider` az általános `api_key` úton magától felveszi és
     feloldja. → kevesebb kód, kevesebb törékenység, verzióálló.

2. **Mappaszerkezet a dokumentált felderítéshez.** Készíts egy
   `plugins/model-providers/hermes-claude-code/__init__.py`‑t, ami importálja a
   csomagot és meghívja `register_provider(profile)`‑t. Két terjesztési mód
   támogatható egyszerre:
   - **(a) Drop‑in / `hermes plugins install owner/repo`**: a repó gyökerében
     legyen ott a `plugins/model-providers/hermes-claude-code/` könyvtár, hogy a
     felhasználói könyvtárba másolva azonnal felderüljön.
   - **(b) pip csomag**: maradhat a `src/hermes_claude_code/` mag, de a telepítés
     egy vékony `__init__.py` shimet tegyen `$HERMES_HOME/plugins/model-providers/
     hermes-claude-code/`‑ba (egy `hermes-claude-code install` parancs vagy
     post‑install lépés), ami csak `from hermes_claude_code.provider import
     register; register()`‑et hív.

3. **`plugin.yaml`: `kind: standalone` → `kind: model-provider`**, és tedd a
   plugin‑könyvtárba (`plugins/model-providers/hermes-claude-code/plugin.yaml`).

4. **Entry point javítás (ha marad pip út):** `hermes-claude-code =
   "hermes_claude_code.plugin:register"` (függvényre, ne modulra), és a `register`
   legyen **argumentum nélkül hívható**, ami `register_provider(profile)`‑t hív.

### P1 — Helyes betöltés és katalógus

5. **`register` szétválasztása.** A provider‑regisztráció (import‑idő, ctx nélkül)
   váljon el a „general plugin" extráktól (hook/CLI/slash). A model‑provider
   `__init__.py` csak regisztráljon. A proxy‑autostartot **ne** session hookra
   bízd (nincs garantált ctx) — tedd **lazy**‑vé: az első `fetch_models()` /
   `/v1/models` / completion híváskor `ensure_proxy_running()`. Az interaktív
   parancsokat (start/stop/status/doctor) hagyd meg külön CLI‑entrypontként
   (`hermes-claude-code …` konzolscript a `pyproject`‑ban — ez már megvan).

6. **`default_aux_model="claude-haiku-4-5-20251001"`** (olcsó/gyors) beállítása,
   hogy a kiegészítő feladatok ne a fő modellt égessék.

7. **Modellkatalógus id‑alapúra.** A `fallback_models` és a `/v1/models` az
   **id**‑ket adja (`claude-opus-4-8`, …); a szép nevet a `display_name` / a
   Hermes label adja. A proxy a kérésben az id‑t fogadja (a mostani
   `MODEL_ID_ALIASES` display→id leképezés így feleslegessé/biztonságosabbá válik).

8. **`model_metadata` / context length** dokumentálása vagy szolgáltatása a
   token‑budget és kompressziós küszöbök helyességéért (ha a Hermes ezt a
   providertől kéri; egyébként a Hermes oldali defaultokra hagyatkozni és ezt
   jelezni a README‑ben).

### P2 — Erősítés, robusztusság, optimalizálás

9. **strict‑mód heurisztikák visszavágása.** A `bridge.py` magyar regexes
   „kitalálós" ágai (URL→`web_extract`, „hermes mappa"→`search_files`,
   permission‑chatter detektálás) **rejtett hibák forrásai**. Erősítés:
   - támaszkodj a modell **natív tool‑use**‑ára (MCP‑n már ott vannak a toolok),
   - a rendszer‑promptban tiltsd a Claude Code natív tooljait (ez már megvan),
   - a heurisztikákat tedd opcionálissá (env‑flag, default OFF) vagy töröld.

10. **`tool_choice` támogatás** (`none`/`required`/`{function}`) leképezése a
    Claude Code felé (vagy legalább a `none`/`required` tiszteletben tartása).

11. **`ProviderProfile` shim szinkronizálása** a valódi mezőkkel
    (`supports_vision_tool_messages`, `fixed_temperature`, `get_hostname`),
    hogy standalone/teszt módban se térjen el a viselkedés.

12. **Optimalizálás (jó alapok, finomítás):** az SDK‑import és URL‑scan memoizálás
    már jó. Érdemes: proxy `keep‑alive`/`idle‑shutdown` (ne maradjon árva
    folyamat), `httpx` kliens újrahasználat, `/v1/models` cache, és a proxy‑port
    ütközés kezelése (ha foglalt, válasszon szabad portot és írja a base_url
    env‑varba).

### Javasolt végállapot (vázlat)

```
hermes-claude-code/
├── pyproject.toml                      # console-script: hermes-claude-code (start/stop/status/doctor/install)
├── src/hermes_claude_code/             # mag: proxy.py, bridge.py, mcp_server.py, config.py, doctor.py, provider.py
│   └── provider.py                     # build_profile() + register() (ctx nélkül, api_key auth)
├── plugins/model-providers/hermes-claude-code/
│   ├── __init__.py                     # from hermes_claude_code.provider import register; register()
│   ├── plugin.yaml                     # kind: model-provider
│   └── README.md
└── tests/
```

Provider profil (lényeg):
```python
ProviderProfile(
    name="hermes-claude-code",
    aliases=("claude-code",),
    display_name="Claude Code",
    api_mode="chat_completions",
    auth_type="api_key",
    env_vars=("HERMES_CLAUDE_CODE_API_KEY", "HERMES_CLAUDE_CODE_BASE_URL"),
    base_url="http://127.0.0.1:35345/v1",
    supports_vision=True,
    default_aux_model="claude-haiku-4-5-20251001",
    fallback_models=("claude-opus-4-8","claude-sonnet-4-6","claude-haiku-4-5-20251001"),
)
```

---

## 7. Prioritizált akciólista (összegzés)

- **P0 (auth/számlázás):** előfizetés (OAuth) használata, **nem** API kulcs;
  `claude` CLI + `claude login` default backend (+ `CLAUDE_CODE_OAUTH_TOKEN`
  headless); `ANTHROPIC_API_KEY` strip a bridge env‑ből + `doctor` figyelmeztetés;
  `--bare` kerülése. (Extra‑usage nélküli működés Anthropic‑oldali politika — tesztelni.)
- **P0 (illeszkedés):** `api_key` auth + placeholder kulcs (csak Hermes→proxy) →
  monkeypatch és kézi registry‑injektálás törlése;
  `plugins/model-providers/<name>/__init__.py` szerkezet;
  `plugin.yaml kind: model-provider`; entry point `…:register` argumentum nélkül.
- **P1 (helyes működés):** provider‑regisztráció és general‑plugin extrák
  szétválasztása; proxy lazy autostart; `default_aux_model`; id‑alapú
  modellkatalógus.
- **P2 (erősítés/optimalizálás):** strict‑heurisztikák visszavágása;
  `tool_choice`; shim‑szinkron; proxy idle‑shutdown + portütközés‑kezelés;
  `/v1/models` cache.

> A nettó hatás: **kevesebb kód** (a két legtörékenyebb rész, a monkeypatch és a
> kézi registry‑hack eltűnik), **dokumentált beillesztési út**, és a kívánt
> „telepítem és ott van a modellválasztóban, natívként viselkedik" élmény.

---

## 8. Implementációs státusz (ebben a branchben elvégezve)

A változtatások **konzervatívak és additívak** — a környezetben nincs valódi
Hermes, ezért a már működő utat nem bontottam meg; minden lépés a 93 zöld teszt
mellett készült.

**Kész (ebben a branchben):**
- ✅ **Könyvtár‑alapú felderítés**: új
  `plugins/model-providers/hermes-claude-code/{__init__.py, plugin.yaml,
  README.md}`; az `__init__.py` import‑időben hívja a `register()`‑et (a
  dokumentált discovery‑út). `pip`‑hiányra `src/` fallback a `sys.path`‑on.
- ✅ **`register(ctx=None)`**: ctx nélkül is hívható (entry‑point/könyvtár‑út),
  nem száll el; a ctx‑függő extrák (hook/CLI/slash) csak ctx esetén futnak.
- ✅ **Entry point javítva**: `hermes_claude_code.plugin:register` (függvényre).
- ✅ **`plugin.yaml kind: standalone → model-provider`** (root + plugin‑könyvtár).
- ✅ **`default_aux_model="Haiku 4.5"`** a profilban (olcsó aux út).
- ✅ **Lazy proxy autostart** a `fetch_models()`‑ben (nem csak session hookon múlik).
- ✅ **Profil‑shim szinkron** (`supports_vision_tool_messages`,
  `fixed_temperature`, `get_hostname`).
- ✅ **Doctor auth megfordítva**: a `claude login` OAuth a zöld jel; az
  `ANTHROPIC_API_KEY` jelenléte **figyelmeztetés** (felülírja az előfizetést,
  API‑áron számláz). + 2 új teszt.
- ✅ **`HERMES_CLAUDE_CODE_FORCE_SUBSCRIPTION=1`**: a bridge a backend
  subprocess env‑jéből kiveszi az `ANTHROPIC_API_KEY`‑t (CLI + SDK `env`),
  PATH stb. megőrizve. Alapból OFF → a jelenlegi viselkedés változatlan.

**Elvégezve (valódi, helyben telepített Hermes ellen validálva — `providers` és
`hermes_cli` importálható ebben a környezetben):**
- ✅ **`auth_type: external_process → api_key`** + a `runtime.py` monkeypatch és
  a `provider.py` kézi `PROVIDER_REGISTRY` injektálás (`register_auth_provider`)
  **eltávolítva**. A profil most `auth_type="api_key"`,
  `env_vars=("HERMES_CLAUDE_CODE_API_KEY","HERMES_CLAUDE_CODE_BASE_URL")`,
  `base_url=localhost`; a `register()` `os.environ.setdefault`-tel beállít egy
  placeholder kulcsot + base URL-t. Valódi Hermesen ellenőrizve: a provider
  felfedeződik, az `auth.py` auto‑extend felveszi a `PROVIDER_REGISTRY`-be
  (`api_key`, `inference_base_url=localhost`), és a `resolve_runtime_provider`
  **nem‑üres** kulccsal + localhost base URL-lel + `chat_completions`-szel
  oldja fel. A `runtime.py` és `tests/test_runtime.py` törölve.
- ✅ **Alias‑ütközés:** a `claude-code` alias (a beépített `anthropic`-é)
  lecserélve `claude-code-agent`-re. Valódi Hermesen ellenőrizve, hogy az
  `anthropic` továbbra is birtokolja a `claude-code`/`claude` aliasokat.
- ✅ **`tool_choice` érvényesítés** a bridge-ben: `none` (nincs tool),
  `required`/`{"type":"any"}` (kötelező tool, rendszerpromptból terelve),
  konkrét függvény (csak az az egy tool exponálva + a többi hívás kiszűrve).
  Tesztelve (`tests/test_tool_choice.py`).
- ✅ **Tiszta szöveg‑LLM mód, amikor Hermes nem ad át toolt** (`_build_options`,
  `bridge.py`): ha nincs exponálandó Hermes‑tool (sem tool nem érkezett, sem
  `tool_choice="none"` nem oltotta ki őket), a bridge explicit
  `tools=[]`‑t állít be a Claude Agent SDK‑n. Enélkül a `ClaudeAgentOptions`
  a saját alapértelmezésével (a teljes natív toolkészlet — Bash, Edit,
  WebFetch stb. — `permission_mode=None`‑nel) indulna, ami headless
  környezetben egy sosem érkező jóváhagyásra várva **beragadhat**. Ezzel
  a Claude Code egy ilyen hívásnál pontosan úgy viselkedik, mint egy sima
  chat‑completions modell: szöveg be, szöveg ki, semmilyen oldalhatás a
  proxyt futtató gépen. (Élőben az SDK‑ból ellenőrizve:
  `ClaudeAgentOptions()` alapértelmezése `permission_mode=None`,
  `allowed_tools=[]`, `can_use_tool=None` — tehát a rés valós volt.)
  Tesztelve (`tests/test_request_options.py`,
  `tests/test_tool_choice.py::test_build_options_none_exposes_no_mcp_server`).

- ✅ **Mappa/manifest rendrakás (repo hygiene).** A repóban három
  `plugin.yaml`‑szerű dolog volt: a gyökér `plugin.yaml`, a checked‑in
  `plugins/model-providers/hermes-claude-code/plugin.yaml`, és az
  `install.py`‑generált verzió. A gyökér‑szintű **törölve** — sem a valódi
  Hermes discovery (csak `__init__.py`‑t importál, YAML‑t sosem olvas be, ezt
  a helyi forrásban ellenőriztem: `grep -rl plugin.yaml` a telepített
  hermes‑agent‑ben nulla találat), sem semmilyen packaging/teszt nem
  hivatkozott rá, és a tartalma **elavult/félrevezető** volt (még
  `ANTHROPIC_API_KEY`‑t javasolt env‑változóként, holott pont azt akadályozzuk
  meg, hogy ez a backend felé szivárogjon). A maradék két másolat (checked‑in
  vs. `$HERMES_HOME`‑ba generált — ezek jogosan különböznek: az egyik a
  „vendor‑drop a hermes‑agent saját bundled mappájába" utat szolgálja `sys.path`
  fallback‑kal, a másik a dokumentált pip‑install utat) most **egy közös
  leírás‑konstansból** (`config.DESCRIPTION`) építkezik, és egy új
  drift‑guard teszt (`tests/test_plugin_manifest_consistency.py`) hibát dob,
  ha a name/kind/version/description mezők a kettő között szétcsúsznak.

**Továbbra is elhalasztva (alacsony prioritás):**
- ⏳ Modellkatalógus id‑alapúra (most display‑nevek + `MODEL_ID_ALIASES`, ami
  **működik**).
- ⏳ A strict‑mód magyar regex heurisztikáinak visszavágása / env‑flag mögé
  tétele (P2). Megjegyzés: ezek a heurisztikák csak akkor sülnek el, ha
  Hermes ténylegesen tool‑listát ad át *és* strict módban vagyunk — a fenti
  tiszta‑LLM javítás (tool nélküli hívás) ettől független, és nem teszi
  feleslegessé ezt a tételt.
- ⏳ Proxy idle‑shutdown + portütközés‑kezelés; `/v1/models` cache (P2).

**Szándékosan elvetett alternatíva:** a tool nélküli hívásnál felmerült az is,
hogy Claude Code fusson **teljesen önállóan a saját natív tooljaival**
(`permission_mode="bypassPermissions"`, Bash/fájlírás engedélyezve) — ez lenne
a „hadd csinálja Claude Code a saját dolgát" legerősebb változata. Ezt a
felhasználó explicit **elutasította** biztonsági megfontolásból (a proxy‑gépen
felügyelet nélküli írás/parancsfuttatás Hermes kérésre túl nagy blast radius),
és a fenti `tools=[]` (teljes letiltás) mellett döntött.

## 9. Verifikálva a valódi forrás ellen (NousResearch/hermes-agent)

A teljes fastruktúrát és a kulcsfájlokat a GitHub API‑n keresztül néztem át
(a git‑klón a környezet relay‑én tiltott). Konkrét megerősítések:

- **`auth.py:440` auto‑extend (idézet):** `if _pp.auth_type != "api_key" or not
  _pp.env_vars: continue` — majd `PROVIDER_REGISTRY[name] = ProviderConfig(...,
  api_key_env_vars=..., base_url_env_var=...)`. Vagyis **bármely `api_key` +
  env‑var provider automatikusan bekerül a registrybe, fájlszerkesztés és
  monkeypatch nélkül.** → A halasztott „api_key‑re váltás" mostantól
  **forrással igazolt**: `auth_type="api_key"`,
  `env_vars=("HERMES_CLAUDE_CODE_API_KEY","HERMES_CLAUDE_CODE_BASE_URL")`,
  `base_url=localhost`, és a `register()` állítson be egy placeholder kulcsot
  (`os.environ.setdefault`). Ekkor a `runtime.py` monkeypatch + a kézi registry
  injektálás **eldobható**.
- **`copilot-acp/__init__.py` (a mi `external_process` analógiánk):** docstring:
  „external ACP subprocess — NOT the standard transport… handled separately in
  run_agent.py". → Az `external_process` **core‑szinten van speciálisan
  kezelve**; harmadik félnek nem generikus. Ezért **indokolt** a jelenlegi
  monkeypatch a mostani (external_process) megközelítéshez — és ezért jobb az
  api_key út.
- **Beépített `anthropic` provider** már létezik:
  `aliases=("claude","claude-oauth","claude-code")`, `api_mode="anthropic_messages"`,
  `env_vars=(...,"CLAUDE_CODE_OAUTH_TOKEN")`, `default_aux_model=
  "claude-haiku-4-5-20251001"`. ⚠️ A mi `claude-code` aliasunk **ütközik** ezzel.
  A providers‑registry last‑writer‑wins → minket adna `claude-code`‑ra, az
  auth‑registry first‑writer‑wins (`setdefault`) → az anthropicot. A split
  zavaró; javasolt a `claude-code` alias elhagyása (pl. `claude-code-agent`).
  Megkülönböztetés: az `anthropic` a **nyers Anthropic API/OAuth**; a mi
  pluginunk a **Claude Code agenten** keresztül megy (saját rendszerprompt,
  toolok, MCP) — két különböző dolog, jogosan külön provider.
- **Minden valódi model‑provider** ugyanúgy néz ki, mint amit a könyvtár‑shimünk
  csinál: `__init__.py` import‑időben `register_provider(ProviderProfile(...))`,
  mellette `plugin.yaml (kind: model-provider)`. → A 8. pont könyvtár‑szerkezete
  pontos.
