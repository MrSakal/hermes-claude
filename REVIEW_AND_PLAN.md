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

## 10. Plugin‑struktúra a hivatalos dokumentáció szerint

Forrás: <https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins>,
keresztellenőrizve a valódi, helyben telepített Hermes forrásával
(`hermes_cli/plugins.py`, ~2300 sor). A doc egy **egyszerűsített** leírás — a
tényleges parser gazdagabb mezőket ismer, ezért a végleges séma a **forrásból**
lett levezetve, nem a doc minimál példájából.

**Fő felismerés: két külön, egymástól független Hermes‑alrendszer érint minket:**

| | `plugins/model-providers/hermes-claude-code/` | `plugins/hermes-claude-code/` (ÚJ) |
| --- | --- | --- |
| Felderítő | `providers._discover_providers` | `hermes_cli.plugins.PluginManager` |
| `plugin.yaml kind` | `model-provider` | `standalone` |
| `register()` hívása | a shim hívja saját magát import‑kor | Hermes importálja a modult, **ő maga hívja** `register(ctx)`‑t |
| Opt‑in kell? | nem — mindig aktív | igen — `hermes plugins enable hermes-claude-code` |
| Mit ad | „Claude Code" a `hermes model`‑ben, chat completions | `on_session_start` proxy‑autostart, `/claude-code` slash, `hermes claude-code` CLI |

**Konkrét forrás‑megerősítések (`hermes_cli/plugins.py`):**
- `PluginManifest` valódi mezői: `name, version, description, author,
  requires_env, provides_tools, provides_hooks, kind, key` — tehát a `kind` és
  `author` **igenis valódi, elemzett mezők** (a doc minimál példája ezt nem
  mutatta be, de a parser kódja igen). `_VALID_PLUGIN_KINDS =
  {"standalone", "backend", "exclusive", "platform", "model-provider"}`.
- A régi (törölt) gyökér `plugin.yaml` `optional_env` mezője **soha nem is
  létezett kulcsként** — a valódi séma `requires_env`. Kettősen indokolt volt
  a törlése.
- `kind == "model-provider"` esetén a `PluginManager` **rögzíti a manifestet
  introspekcióhoz, de nem importálja újra a modult** — „handled by providers/
  discovery" — így a modell‑provider könyvtár akkor sem okoz dupla
  regisztrációt, ha a général scanner is meglátja (ellenőrizve: a bundled
  scan explicit kihagyja a `model-providers` nevű alkönyvtárat
  `skip_names`‑szel, de a **user‑scan nem** — `_scan_directory(user_dir,
  source="user")` skip_names nélkül fut, tehát kategória‑könyvtárként
  belenéz a `$HERMES_HOME/plugins/model-providers/`‑be is; ez ártalmatlan a
  fenti no‑op ág miatt).
- `_scan_entry_points()` **csak `name`‑et és `path`‑ot tölt ki** a
  `PluginManifest`‑ben — `version/description/author/kind` mind üresen
  marad (`kind` a dataclass‑default `"standalone"`‑ra esik). → A korábbi
  `[project.entry-points."hermes_agent.plugins"]` bejegyzésünk emiatt **üres
  metaadattal** jelent volna meg a `hermes plugins list`‑ben, ÉS mivel a
  „later source overrides earlier" szabály szerint a pip‑forrás (4.) a
  user‑könyvtár (2.) UTÁN fut le azonos kulcsra, a gazdagabb könyvtár‑alapú
  manifestünket **csendben felülírta volna** az üres. → **A pip entry point
  törölve**, helyette valódi könyvtár‑alapú általános plugin
  (`plugins/hermes-claude-code/`).
- `kind == "standalone"` (ide tartozik minden entry‑point plugin és a most
  hozzáadott általános plugin is) **mindig opt‑in**: „Everything else
  (standalone, user‑installed backends, entry‑point plugins) is opt‑in via
  plugins.enabled." Ez könyvtár‑ vagy pip‑alapú forrástól függetlenül igaz —
  a váltás a manifeszt‑minőségen javított, az opt‑in kötelezettségen nem.
- `_load_plugin`: `register_fn = getattr(module, "register", None); ...
  register_fn(ctx)` — a **PluginManager saját maga hívja** a `register`‑t egy
  valódi `PluginContext`‑tel. Ez ellentétes a modell‑provider shim
  konvenciójával (ami saját magát hívja import‑kor) — emiatt az új
  `plugins/hermes-claude-code/__init__.py` **NEM hívhatja** a `register()`‑t,
  csak exponálnia kell modul‑attribútumként, különben duplán futna.

**Élőben, valódi Hermesen validálva** (`hermes_cli.plugins.get_plugin_manager`,
`discover_plugins`, ideiglenes `$HERMES_HOME` + `config.yaml`):
```
BEFORE enable:  enabled=False, error="not enabled in config (run
                 `hermes plugins enable hermes-claude-code` to activate)"
AFTER  enable:  enabled=True, hooks_registered=['on_session_start'],
                 commands_registered=['claude-code'],
                 manifest name/version/description/author mind helyesen
                 kitöltve (nem üres, ahogy a pip‑entry‑point út adta volna)
```

**Elvégzett átalakítás:**
- ✅ Új `plugins/hermes-claude-code/{__init__.py, plugin.yaml, README.md}` —
  `kind: standalone`, `provides_hooks: [on_session_start]`, `__init__.py`
  **nem** hívja a `register()`‑t.
- ✅ `install.py`: mindkét könyvtárat írja (`provider_plugin_dir` +
  `general_plugin_dir`); `install()` visszaadja a
  `hermes plugins enable hermes-claude-code` következő lépést.
- ✅ `pyproject.toml`: a `[project.entry-points."hermes_agent.plugins"]` blokk
  **törölve** (ütközne az új könyvtár‑manifesttel).
- ✅ `plugin.py` docstringjei frissítve a két‑alrendszeres modellre.
- ✅ `cli.py` telepítés‑utáni üzenete tartalmazza az opt‑in lépést.
- ✅ README.md „Two plugin subsystems" táblázat + install lépések frissítve.
- ✅ `tests/test_plugin_manifest_consistency.py` és `tests/test_install.py`
  kibővítve mindkét shim‑párra.

## 11. A modell‑provider plugin doksi szerinti mezőellenőrzés

Forrás: <https://hermes-agent.nousresearch.com/docs/developer-guide/model-provider-plugin>.
Ez a doc kifejezetten a `ProviderProfile` mezőiről és a `plugin.yaml` sémáról
szól — a 10. pontban tárgyalt „két alrendszer" kérdéstől független, tisztán a
**mit kell tartalmaznia a providerünknek** kérdés.

**Mezőnkénti összevetés (`provider.py` `build_profile`):**
- `name/aliases/api_mode/display_name/description/env_vars/base_url/
  auth_type/fallback_models/default_aux_model` — mind pontosan illeszkedik a
  doksi táblázatához és a korábbi (9. pont) forrás‑ellenőrzéshez.
- **`signup_url` hiányzott** — ez egy valódi, a doksi által is felsorolt mező
  („Shown during first‑run setup"), amit korábban nem állítottunk be. Mivel a
  hitelesítés `claude login` (CLI OAuth), nem webes API‑kulcs‑regisztráció,
  egy generikus claude.ai‑link helyett a **saját repó Install szekciójára**
  mutat (`SIGNUP_URL` konstans, `config.py`) — ez ténylegesen segít a
  felhasználónak, a generikus link nem mondaná el a `claude login` +
  `hermes-claude-code install` lépéseket.
- `models_url` — szándékosan üresen hagyva; a doksi szerinti alapértelmezés
  (`{base_url}/models`) pontosan egyezik a proxynk útvonalával, és a saját
  `fetch_models()` felülírásunk úgyis közvetlenül ezt az URL‑t építi.
- **Felülírható hookok** (`prepare_messages`, `build_extra_body`,
  `build_api_kwargs_extras`) — szándékosan **nincsenek felülírva**: ezek a
  Hermes‑oldali (kliens‑oldali) kimenő HTTP‑kérés testreszabására valók,
  nálunk viszont a proxy sima, változatlan OpenAI `chat/completions` kérést
  kap, és minden Claude Code‑specifikus fordítást szerver‑oldalon, a
  `bridge.py`‑ban végzünk. Ez tudatos döntés, nem hiányosság — most kódkommentben
  is dokumentálva (`provider.py`).
- `plugin.yaml` mezősorrendje/tartalma pontosan egyezik a doksi
  `acme-inference` sablonjával (`name, kind, version, description, author`);
  a verziószám idézőjelezését egységesítettem (`"0.1.0"`) mindhárom helyen
  (checked‑in provider‑manifest, checked‑in general‑plugin‑manifest,
  `install.py` generált verziók).
- **„Distribution via pip" szakasz** a doksiban egy `hermes_agent.plugins`
  entry pointot javasol modell‑provider terjesztéshez is
  (`acme-inference = "acme_hermes_plugin:register"`). Ezt **szándékosan NEM
  vezettük be újra** — a 10. pontban forrásból igazoltuk, hogy a
  `_scan_entry_points()` a `kind`‑ot mindig `"standalone"`‑ra hagyja (nincs
  forrás‑sniffelés, mint a könyvtár‑alapú manifestnél), tehát egy ilyen
  entry point **opt‑in‑köteles és üres metaadatú** lenne — pont az a
  probléma, amit a 10. pontban a general‑plugin fél kapcsán már kijavítottunk.
  A könyvtár‑alapú út (bundled + user, mindkettő lefedve az
  `install.py`/checked‑in shimekkel) a doksi „Discovery Mechanism" listáját
  (bundled → user → legacy single‑file) teljesen és kollízió nélkül fedi.
- README kiegészítve a doksi saját ellenőrző receptjével
  (`hermes -z "hello" --provider hermes-claude-code -m sonnet`, `hermes doctor`)
  és egy „ProviderProfile field reference" táblázattal.

## 12. `hermes_home()` — valódi hiba Windows‑on (megtalálva a felhasználó saját gépén)

A felhasználó gépén (Windows) a valódi, telepített Hermes ellen futtatva:
`hermes_constants.get_hermes_home()` → `%LOCALAPPDATA%\hermes`
(`C:\Users\<user>\AppData\Local\hermes`), **NEM** `~/.hermes`. A mi
`config.py hermes_home()`‑unk viszont eddig feltétel nélkül `Path.home() /
".hermes"`‑t adott vissza, ha a `HERMES_HOME` env változó nincs beállítva.

**Ez azt jelentette volna, hogy `HERMES_HOME` explicit beállítása nélkül a
`hermes-claude-code install` Windows‑on egy olyan könyvtárba írt volna
(`~/.hermes`), amit a valódi Hermes sosem néz meg** — az `install` sikert
jelentett volna, de a provider sosem jelenne meg a `hermes model`‑ben, néma,
nehezen debuggolható hibaként.

**Javítás** (`config.py hermes_home()`): pontosan lemásolja a valódi
`hermes_constants._get_platform_default_hermes_home()` logikáját —
`win32`‑n `%LOCALAPPDATA%\hermes` (fallback `~/AppData/Local/hermes`, ha a
env var üres), egyébként `~/.hermes`. `HERMES_HOME` env var továbbra is
mindig felülír mindent.

Élőben ellenőrizve a felhasználó gépén: a javított `hermes_home()` és a
valódi `hermes_constants.get_hermes_home()` **pontosan ugyanazt az útvonalat**
adja vissza (`C:\Users\...\AppData\Local\hermes`). Új teszt:
`tests/test_config.py` (4 eset: env‑var felülír, win32 LOCALAPPDATA‑val,
win32 LOCALAPPDATA nélkül, nem‑win32).

## 13. Miért NEM jó a Hermes dashboard „Telepítés GitHubról" installere ide

A felhasználó a valódi Hermes web‑dashboardján (`/plugins` oldal) mutatott egy
GUI dobozt („Telepítés GitHubról / Git URL‑ről", „Engedélyezés a telepítés
után" kapcsolóval), és megkérdezte, hogy ezen keresztül lehet‑e telepíteni.
Elolvastam a mögötte futó valódi kódot (`hermes_cli/plugins_cmd.py`,
`_install_plugin_core`, ~1940 sor) — **ez a mechanizmus strukturálisan
alkalmatlan a mi pluginunkra**, két okból, forrásból igazolva:

1. **Mindig lapos célba telepít**: `plugins_dir = _plugins_dir()` = mindig
   `$HERMES_HOME/plugins/<name>/`. Nincs benne semmi, ami a
   `model-providers/` alkategóriát ismerné vagy oda irányítana. Ha a
   `plugins/model-providers/hermes-claude-code` subdirt adnánk meg
   azonosítóként, a fájlok **laposan** landolnának — a
   `providers._discover_providers()` viszont *kizárólag* a
   `plugins/model-providers/<name>/` útvonalat nézi (bundled + user), ezt a
   lapos helyet sosem. A `hermes_cli.plugins.PluginManager` saját maga is
   látná (hisz oda IS lapos scannel), a `plugin.yaml`‑ban deklarált
   `kind: model-provider` miatt viszont **explicit kihagyná** („handled by
   providers/ discovery" — csak épp az a másik rendszer sosem néz oda). Az
   eredmény: a Bővítmények lista „telepítve, engedélyezve"‑t mutatna, miközben
   a „Claude Code" **sosem jelenne meg** a `hermes model`‑ben.
2. **Sosem futtat `pip install`‑t** — `_install_plugin_core` tiszta `git
   clone --depth 1` + `shutil.move`, semmilyen csomagtelepítési lépés nincs
   benne. A `httpx`/`fastapi`/`uvicorn`/`claude-agent-sdk` sosem kerülne
   telepítésre, a shim `ModuleNotFoundError`‑ral elszállna.

Mindkét pont **Hermes saját installerének korlátja**, nem a mi
mappaszerkezetünké — semmilyen átrendezéssel nem javítható a mi oldalunkról.
Megfontoltam egy „nulla‑függőségű lite" átírást is (csak `claude` CLI
subprocess + Python stdlib `http.server`, `claude-agent-sdk`/FastAPI/uvicorn
elhagyásával), ami tényleg GUI‑telepíthetővé tenné — a felhasználó a gazdag
verzió (streaming, natív tool‑hidalás, vízió) megtartása mellett döntött, egy
külön `pip install` lépés elfogadásával.

**Ehelyett**: az `AGENTS.md`‑ben (l. 14. pont) egy „Do NOT" szakasz
magyarázza el ezt, forrásra hivatkozva, hogy senki (ember vagy AI-ügynök) ne
próbálja meg ezt az utat és ne csodálkozzon a néma hibán.

### Kompenzáció: automatikus engedélyezés telepítéskor

Mivel a `pip install` lépés úgyis elkerülhetetlen, a hátralévő kézi lépések
számát minimalizáltuk: az `install.py` mostantól **automatikusan bekapcsolja**
az általános plugint. Első nekifutásra ez a Hermes saját, belső
`hermes_cli.config.load_config`/`save_config` függvényeinek újrahasznosításával
történt — ezt a felhasználó explicit visszautasította (l. 14. pont: „a
dokumentációjukhoz tartsuk magunkat, az a legbiztosabb"), és **a dokumentált
CLI‑parancsra** (`hermes plugins enable <name>`) váltottunk subprocess‑ből
hívva, ami stabilabb szerződés egy belső, nem‑dokumentált API‑nál.

Élőben validálva: a subprocess‑hívás egy előre feltöltött, más beállításokat
tartalmazó ideiglenes `config.yaml` ellen helyesen hozzáadta a
`hermes-claude-code`‑ot az `enabled` listához, más beállítást nem érintett,
és ismétlésre nem duplikált. Egy teljes, tiszta `install()`‑hívás után (kézi
lépés nélkül) a friss `PluginManager` már `enabled=True`‑t mutat, a hook és a
`/claude-code` parancs regisztrálva van, ÉS a modell‑provider is felfedezve —
mindezt egyetlen függvényhívásból. Lásd a 14. pontot a CLI‑váltás közben
talált valódi hibáért (a hiányzó `--no-allow-tool-override` miatti lefagyás).

Tesztek: `tests/test_install.py` (`_auto_enable_general_plugin`
dependency‑injectált `which`/`run`‑nal — a pontos dokumentált parancsot
hívja‑e, `PATH`‑on‑nincs‑hermes eset, nemnulla exit code, kivétel).

## 14. Felhasználói visszajelzés: dokumentált API előny, auth‑tisztázás, README‑szétválasztás

Három kérés érkezett a 13. pont munkája után:

1. **„Tartsuk magunkat a dokumentációjukhoz, az a legbiztosabb."** — A 13.
   pontban leírt `_auto_enable_general_plugin` első verziója a Hermes
   **belső**, nem‑dokumentált `hermes_cli.config.load_config`/`save_config`
   függvényeit hívta közvetlenül. Ez működött (élőben validálva), de
   instabilabb szerződés, mint egy dokumentált CLI‑parancs — bármikor
   megváltozhat a Hermes egy jövőbeli verziójában, tesztelés/figyelmeztetés
   nélkül. **Váltás**: `subprocess.run([hermes_exe, "plugins", "enable",
   PROVIDER_NAME, "--no-allow-tool-override"])` — a Hermes saját, dokumentált
   „Plugin Management Commands" parancsa.

   **Eközben talált valódi hiba**: a `--no-allow-tool-override` flag nélkül a
   `hermes plugins enable` **lefagyott / 30 másodperces timeoutba futott**.
   Forrásból kiderült miért: `cmd_enable` minden nem‑bundled pluginnál
   interaktívan rákérdez a „tool override" jogosultságra
   (`_resolve_tool_override_grant`, `allow_tool_override=None` esetén
   `input()`‑et hív), és subprocess‑ből (nincs TTY, `capture_output=True`)
   ez örökre blokkol. A mi pluginunk **sosem regisztrál toolt**
   (`ctx.register_tool` sehol nincs hívva), tehát az elutasítás funkcionálisan
   semleges — a `--no-allow-tool-override` flag hozzáadása megoldotta,
   élőben megerősítve: 1.4 másodperc alatt visszatér, `returncode: 0`.

   **Teszt‑biztonsági közeli hiba**: az új subprocess‑alapú teszt egyik
   első verziója a VALÓDI `shutil.which`/`subprocess.run`‑t használta volna
   (nem injektálva) — és kiderült, hogy ezen a gépen a `hermes` bináris
   **ténylegesen a PATH‑on van** (`which hermes` megtalálja). Ha lefut, ez a
   teszt a felhasználó **éles** `config.yaml`‑ját módosította volna. Még
   futtatás előtt észrevettem és javítottam — a tesztet explicit
   `monkeypatch.setattr(shutil, "which", ...)`/`subprocess.run`‑nal védtem,
   AssertionError‑ral, ha mégis meghívná a valódit. Ellenőrizve a valódi
   `config.yaml` módosítási idejével: érintetlen maradt (utolsó módosítás a
   mai munka előttről).

2. **„Csak `claude login` kell, API‑kulcsot nem szeretnék ebben a
   pluginban."** — A README auth‑szakasza korábban a `claude
   setup-token`/`CLAUDE_CODE_OAUTH_TOKEN` headless utat egyenrangúan mutatta
   a `claude login`‑nal a fő telepítési folyamatban. Ez összemosódhat az
   „API‑kulcs" fogalmával, holott ez ugyanaz az OAuth‑folyam, csak
   non‑interaktív változatban. Az új `README.md` fő útja **kizárólag**
   `claude login`; a headless/szerver változat átkerült az `AGENTS.md`‑be,
   mint másodlagos, egyértelműen „még mindig a te előfizetésed, nem
   API‑kulcs" felirattal ellátott opció.

3. **„A README‑t úgy szerkeszd, hogy az AI is értse, hogyan kell telepíteni,
   plusz legyen egy sima README is."** — A `README.md` (256 sor, sűrű
   technikai táblázatokkal, forrás‑idézetekkel) helyett:
   - **`README.md`** — rövid, sima, ember‑barát: mi ez, 4 lépéses telepítés,
     használat, konfiguráció‑táblázat, fejlesztés. ~90 sor.
   - **`AGENTS.md`** (új) — explicit, procedurális, AI‑ügynök számára
     írt telepítési/ellenőrzési/hibaelhárítási útmutató: pontos parancsok,
     „Check:"/„If it fails:" minden lépés után, „Do NOT" szakasz (GitHub‑
     install GUI, `--no-allow-tool-override` hiánya, `ANTHROPIC_API_KEY`),
     hibaelhárító táblázat, uninstall. Ez viszi tovább a korábbi README
     „Why not Install from GitHub?" és „Two plugin subsystems" mélytechnikai
     tartalmát, immár a hibaelhárítás kontextusában.
   - **`REVIEW_AND_PLAN.md`** — változatlanul az építkezés közbeni
     mérnöki napló/audit‑trail (ez a fájl).

   Frissítve a kereszthivatkozások is: a `plugins/model-providers/
   hermes-claude-code/README.md` és `plugins/hermes-claude-code/README.md`
   most az `AGENTS.md`‑re mutat a törölt README‑szakaszok helyett, és az
   utóbbi már a dokumentált CLI‑parancsot írja le a `load_config`/
   `save_config` helyett (elavult volt a 13. pont utáni váltás óta).
