# Monitor de disponibilidad — Illas Cíes (9-14 agosto)

Comprueba periódicamente si hay disponibilidad en el calendario de
[autorizacionillasatlanticas.xunta.gal](https://autorizacionillasatlanticas.xunta.gal/illasr/iniciarReserva)
para Illas Cíes / Visitantes, y te avisa por email cuando aparece
disponibilidad nueva entre el 9 y el 14 de agosto de 2026.

## ✅ Selectores verificados con `playwright codegen`

Estos selectores están **confirmados contra la web real** (los sacaste
tú con `playwright codegen`, dos veces):

1. `Visitantes` (primer enlace = Illas Cíes)
2. Campo `*Número de prazas:` → se rellena con el nº de plazas
3. Enlace de naviera (en tu caso `"Mar"`, posición `nth(1)`) — **la
   disponibilidad depende de qué naviera/puerto elijas**, no es
   genérica para toda la isla
4. **Cada día tiene un enlace clicable que, al hacer click, muestra un
   cuadro informativo con "Prazas libres: N"** (id tipo
   `cadroInformativoPlazas-N`). Esto es mucho más preciso que solo
   mirar si el día es clicable: en tu segunda prueba el día 13 SÍ era
   clicable pero tenía **0 prazas libres**. Por eso el script ahora
   hace click en cada uno de los 6 días objetivo, lee ese número, y
   solo lo marca como disponible si `plazas_libres >= NUM_PRAZAS`.

No hemos visto indicios de que estos clicks de "previsualización"
creen una reserva provisional (esa solo se genera, según la propia
documentación oficial, al completar los 4 pasos del proceso). Aun así,
si algún día notas comportamientos raros, corre primero en modo
`--inspect` para revisar qué está pasando antes de lanzarlo en bucle
en GitHub Actions.

### Cosa a vigilar: dos meses a la vez

Como el calendario probablemente muestra el mes actual y el
siguiente a la vez, los días 9-14 pueden existir simultáneamente en el
panel de julio y en el de agosto. El script intenta localizar
específicamente el panel de "Agosto 2026" (`find_month_container`)
antes de buscar los días dentro de él. Si aun así ves en el log
"aviso, N coincidencias" para algún día, revisa `debug/04_calendar.html`
y dime qué ves para afinar `find_month_container`.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

python check_availability.py --inspect
```

Revisa las capturas generadas:
- `01_home.png`
- `02_after_visitantes.png`
- `03_after_naviera.png` — confirma que la naviera/puerto es el que
  quieres (si no, cambia `NAVIERA_LINK_NAME`/`NAVIERA_INDEX` en el
  script)
- `04_calendar.png` — captura final tras comprobar los 6 días (fíjate
  en los cuadros de "Prazas libres" que hayan quedado abiertos)

Y en la consola verás algo así:

```
Día 9: 4 prazas libres
Día 10: 0 prazas libres
Día 11: no es un enlace clicable -> sin plazas
...
```

## 2. Configura el email (Gmail como ejemplo)

Si usas Gmail, crea una "contraseña de aplicación" en tu cuenta
Google (Cuenta → Seguridad → Verificación en dos pasos → Contraseñas
de aplicaciones) — no funciona con tu contraseña normal.

En tu repositorio de GitHub, ve a **Settings → Secrets and variables →
Actions → New repository secret** y crea:

| Secret        | Valor (ejemplo Gmail)     |
|---------------|----------------------------|
| `SMTP_SERVER` | `smtp.gmail.com`           |
| `SMTP_PORT`   | `587`                      |
| `SMTP_USER`   | `tucuenta@gmail.com`       |
| `SMTP_PASS`   | la contraseña de aplicación |
| `EMAIL_TO`    | email donde quieres el aviso |

## 3. Sube el proyecto a GitHub y activa Actions

```bash
git add .
git commit -m "Monitor disponibilidad Illas Cíes"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

El workflow (`.github/workflows/check-cies.yml`) se ejecuta cada 20
minutos automáticamente. También puedes lanzarlo a mano desde la
pestaña **Actions → Comprobar disponibilidad Illas Cíes → Run
workflow** (marca la casilla `inspect` si quieres correrlo en modo
diagnóstico y bajarte las capturas como artifact).

## 4. Cómo evita spam de emails

El script guarda en `state.json` (dentro del propio repo) qué días
estaban disponibles en la última comprobación, y solo envía email
cuando aparece disponibilidad **nueva** que antes no estaba. El
workflow hace commit automático de ese fichero tras cada ejecución.

## Nota sobre el número de plazas (2 personas)

Con `NUM_PRAZAS = "2"` el script solo marca un día como disponible si
su cuadro de "Prazas libres" muestra 2 o más. Es una comprobación
exacta, no una aproximación — es la misma cifra que vería un usuario
real al pinchar ese día en la web.

## Archivos

- `check_availability.py` — script principal
- `requirements.txt` — dependencias
- `.github/workflows/check-cies.yml` — automatización
- `state.json` — se crea solo, no lo edites a mano
- `debug/` — solo se genera en modo `--inspect`

