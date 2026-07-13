#!/usr/bin/env python3
"""
Monitor de disponibilidad para la reserva de autorización de visita
a las Illas Cíes (autorizacionillasatlanticas.xunta.gal).

IMPORTANTE - LÉEME ANTES DE USAR:
Esta web es una aplicación Java/JSF: casi todas las acciones (elegir
isla, tipo de visitante, elegir naviera...) se hacen mediante
JavaScript sobre la misma URL. Los selectores de este script están
confirmados contra la web real (grabados con `playwright codegen`),
incluyendo la lectura del cuadro "Prazas libres:" que aparece al
hacer click en cada día. Por cada comprobación, el script hace click
en los 6 días objetivo (9-14 de agosto) para leer su cuadro de plazas
disponibles; esto NO parece iniciar ninguna reserva provisional (esa
solo se genera al completar los 4 pasos del proceso de autorización),
pero si algo cambiase en la web y sí ocurriera, revisa primero en
modo --inspect.

Por eso el script tiene un modo --inspect: te guarda una captura de
pantalla y el HTML de cada paso en ./debug/ para que puedas ver
exactamente qué está pasando y ajustar los selectores si hace falta
(o pegármelos a mí para que te ayude a afinarlos).

Uso:
    python check_availability.py --inspect      # modo diagnóstico (recomendado la 1a vez)
    python check_availability.py                # modo normal (comprueba y notifica)
"""

import argparse
import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_URL = "https://autorizacionillasatlanticas.xunta.gal/illasr/inicio"

# ---------------------------------------------------------------------
# CONFIGURACIÓN — ajusta aquí lo que necesites
# ---------------------------------------------------------------------
# Confirmado con playwright codegen contra la web real:
VISITOR_TYPE = "Visitantes"       # "Visitantes" | "Campistas" | "Veciños"
ISLAND_INDEX = 0                  # .first -> Illas Cíes es el primer "Visitantes" en el DOM

NUM_PRAZAS = "1"                  # número de plazas a comprobar

# Naviera / punto de embarque. En tu grabación elegiste el link "Mar"
# en la posición nth(1) (probablemente "Mar de Ons" en un puerto
# concreto). LA DISPONIBILIDAD ES ESPECÍFICA DE ESTA NAVIERA/PUERTO,
# no genérica para toda la isla. Si quieres comprobar otra, cambia
# estos dos valores (mira debug/03_after_naviera.png para confirmar
# cuál se ha seleccionado realmente).
NAVIERA_LINK_NAME = "Mar"
NAVIERA_INDEX = 1

TARGET_YEAR = 2026
TARGET_MONTH_NAME = "Agosto"      # tal como aparece en el calendario
TARGET_DAYS = [9, 10, 11, 12, 13, 14]

STATE_FILE = Path("state.json")
DEBUG_DIR = Path("debug")


def log(msg):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


MAX_NAV_ATTEMPTS = 4  # la web a veces no navega al primer click; reintentamos


def go_to_calendar(page, debug=False):
    """
    Navega desde la home hasta el paso con el calendario. Esta web a
    veces se queda "colgada" en la home tras hacer click en
    'Visitantes' (confirmado por el usuario con capturas reales), así
    que reintentamos varias veces recargando si hace falta.
    """
    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)

    for attempt in range(1, MAX_NAV_ATTEMPTS + 1):
        log(f"Intento {attempt}/{MAX_NAV_ATTEMPTS} de navegar a la ficha de Visitantes...")
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)

        try:
            page.get_by_role("link", name=VISITOR_TYPE).first.wait_for(
                state="visible", timeout=20000
            )
        except PWTimeout:
            pass

        if debug and attempt == 1:
            page.screenshot(path=str(DEBUG_DIR / "01_home.png"), full_page=True)
            (DEBUG_DIR / "01_home.html").write_text(page.content(), encoding="utf-8")

        visitantes_links = page.get_by_role("link", name=VISITOR_TYPE)
        count = visitantes_links.count()
        if count <= ISLAND_INDEX:
            log(f"  No se encontraron suficientes enlaces '{VISITOR_TYPE}' ({count}); reintentando...")
            continue

        # Empujoncito extra: a veces hace falta interactuar primero con
        # la tarjeta de la isla antes de que el click en "Visitantes"
        # funcione (visto en grabaciones reales del usuario).
        try:
            page.get_by_text(re.compile(rf"Illas Cíes.*{VISITOR_TYPE}", re.S)).first.click(
                timeout=2000
            )
        except Exception:
            pass

        visitantes_links.nth(ISLAND_INDEX).click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        # ¿Navegó de verdad? Comprobamos con la migaja de pan
        # "Vostede atópase en: Illas Cíes" o con el propio campo de
        # plazas, lo que aparezca primero.
        navigated = False
        try:
            page.get_by_text(re.compile("Vostede atópase en", re.I)).first.wait_for(
                state="visible", timeout=6000
            )
            navigated = True
        except PWTimeout:
            try:
                page.get_by_label("*Número de prazas:").wait_for(state="visible", timeout=3000)
                navigated = True
            except PWTimeout:
                navigated = False

        if debug:
            page.screenshot(path=str(DEBUG_DIR / f"02_intento{attempt}.png"), full_page=True)

        if navigated:
            log(f"  Navegación correcta en el intento {attempt}")
            break
        else:
            log(f"  La página no navegó tras el click (intento {attempt}); reintentando...")
    else:
        raise RuntimeError(
            f"No se consiguió navegar a la ficha de Visitantes tras {MAX_NAV_ATTEMPTS} intentos. "
            f"Revisa las capturas debug/02_intento*.png"
        )

    if debug:
        page.screenshot(path=str(DEBUG_DIR / "02_after_visitantes.png"), full_page=True)
        (DEBUG_DIR / "02_after_visitantes.html").write_text(page.content(), encoding="utf-8")

    # Número de plazas. Puede haber un paso intermedio (checkbox de
    # condiciones / botón "Continuar") antes de que aparezca este
    # campo, así que lo intentamos primero directamente y, si no
    # aparece, buscamos y resolvemos ese posible paso intermedio.
    prazas_field = page.get_by_label("*Número de prazas:")
    try:
        prazas_field.wait_for(state="visible", timeout=8000)
    except PWTimeout:
        log("El campo de plazas no apareció a la primera; buscando un paso intermedio "
            "(checkbox de condiciones / botón Continuar)...")

        handled = False
        try:
            checkbox = page.locator("input[type=checkbox]").first
            if checkbox.count() > 0 and checkbox.is_visible(timeout=3000):
                checkbox.check()
                log("Checkbox de condiciones marcado")
                handled = True
        except Exception:
            pass

        try:
            continuar = page.get_by_role("button", name=re.compile("continuar", re.I))
            if continuar.count() > 0 and continuar.first.is_visible(timeout=3000):
                continuar.first.click()
                page.wait_for_load_state("domcontentloaded")
                log("Click en 'Continuar'")
                handled = True
        except Exception:
            pass

        if debug:
            page.screenshot(path=str(DEBUG_DIR / "02b_after_intermedio.png"), full_page=True)
            (DEBUG_DIR / "02b_after_intermedio.html").write_text(page.content(), encoding="utf-8")

        try:
            prazas_field.wait_for(state="visible", timeout=10000)
        except PWTimeout:
            raise RuntimeError(
                "El campo '*Número de prazas:' nunca apareció, ni tras intentar un paso "
                f"intermedio (checkbox/Continuar encontrado: {handled}). "
                "Revisa debug/02_after_visitantes.png, debug/02_after_visitantes.html "
                "y debug/02b_after_intermedio.png para ver qué se muestra realmente en pantalla."
            )

    prazas_field.click()
    prazas_field.fill(NUM_PRAZAS)
    log(f"Número de prazas fijado a {NUM_PRAZAS}")

    # Naviera / punto de embarque
    naviera_links = page.get_by_role("link", name=NAVIERA_LINK_NAME)
    naviera_count = naviera_links.count()
    log(f"Encontrados {naviera_count} enlaces de naviera con nombre '{NAVIERA_LINK_NAME}'")
    if naviera_count <= NAVIERA_INDEX:
        raise RuntimeError(
            f"No se encontró el enlace de naviera nº {NAVIERA_INDEX}. "
            f"Revisa debug/02_after_visitantes.png y ajusta NAVIERA_LINK_NAME/NAVIERA_INDEX"
        )
    naviera_links.nth(NAVIERA_INDEX).click()
    page.wait_for_load_state("domcontentloaded")

    if debug:
        page.screenshot(path=str(DEBUG_DIR / "03_after_naviera.png"), full_page=True)
        (DEBUG_DIR / "03_after_naviera.html").write_text(page.content(), encoding="utf-8")

    # El calendario ya muestra por defecto el mes correcto (no hace
    # falta navegar), pero la disponibilidad (colores verde/rojo y los
    # días que pasan a ser enlaces clicables) se carga por AJAX unos
    # segundos después de llegar aquí. Esperamos a que eso ocurra antes
    # de comprobar ningún día.
    wait_for_calendar_ready(page)

    if debug:
        page.screenshot(path=str(DEBUG_DIR / "03b_after_month.png"), full_page=True)
        (DEBUG_DIR / "03b_after_month.html").write_text(page.content(), encoding="utf-8")


def wait_for_calendar_ready(page, timeout_ms=20000):
    """
    Tras elegir la naviera, el calendario carga la disponibilidad de
    forma asíncrona: los días pasan de texto plano (negro, sin
    colorear) a enlaces coloreados en verde/rojo según haya o no
    plazas. Esperamos a que aparezca al menos un día como enlace antes
    de comprobar nada, o se acaba el tiempo de espera.
    """
    waited = 0
    step = 500
    any_day_link = page.get_by_role("link", name=re.compile(r"^\d{1,2}$"))
    while waited < timeout_ms:
        if any_day_link.count() > 0:
            log(f"Calendario cargado (tras {waited}ms), ya hay días como enlaces")
            return True
        page.wait_for_timeout(step)
        waited += step
    log("Aviso: el calendario no mostró ningún día como enlace tras esperar "
        f"{timeout_ms}ms. Puede que no haya disponibilidad en absoluto, o que "
        "la carga tarde más de lo esperado.")
    return False


def find_month_container(page):
    """
    Devuelve el locator del panel del calendario correspondiente a
    TARGET_MONTH_NAME/TARGET_YEAR (p.ej. "Agosto 2026"), o None si no
    se encuentra un contenedor específico. Prioriza el contenedor que
    realmente contiene los enlaces numéricos de los días, porque a veces
    el título del mes aparece en wrappers más amplios o más estrechos
    que no contienen la grilla del calendario.
    """
    pattern = re.compile(rf"{TARGET_MONTH_NAME}\s*{TARGET_YEAR}", re.I)

    explicit = page.locator("#calendario")
    if explicit.count() > 0 and explicit.filter(has_text=pattern).count() > 0:
        return explicit.first

    candidates = page.locator("table, div").filter(has_text=pattern)
    n = candidates.count()
    if n == 0:
        log(f"No se encontró contenedor específico para '{TARGET_MONTH_NAME} {TARGET_YEAR}', "
            f"se buscará en toda la página")
        return None

    for idx in range(n - 1, -1, -1):
        candidate = candidates.nth(idx)
        day_links = candidate.get_by_role("link", name=re.compile(r"^\d{1,2}$"))
        if day_links.count() > 0:
            return candidate

    # Fallback: el contenedor más específico suele ser el último en el
    # listado de `filter`, aunque no contenga enlaces de día directamente.
    return candidates.last


def read_plazas_for_day(page, day, scope, debug=False):
    """
    Hace click en el enlace del día (dentro de `scope`, normalmente el
    panel de agosto) y lee el número de "Prazas libres:" del cuadro
    informativo que aparece (id tipo cadroInformativoPlazas-N).

    Devuelve un int con las plazas libres, o None si no se pudo
    determinar (día no clicable, o no apareció el cuadro).
    """
    day_link = scope.get_by_role("link", name=str(day), exact=True)
    n = day_link.count()
    if n == 0:
        log(f"  Día {day}: no es un enlace clicable -> sin plazas")
        return None
    if n > 1:
        log(f"  Día {day}: aviso, {n} coincidencias en el panel; se usa la primera")

    day_link.first.click()

    # La web no siempre crea un cuadro nuevo; a veces reutiliza el mismo
    # contenedor y solo actualiza su contenido. Por eso esperamos a que
    # cualquiera de los cuadros existentes muestre el texto con "Prazas libres".
    for _ in range(20):
        cards = page.locator("[id^='cadroInformativoPlazas-']")
        for idx in range(cards.count()):
            text = cards.nth(idx).inner_text()
            match = re.search(r"prazas?\s+libres?:?\s*(\d+)", text, re.I)
            if match:
                plazas = int(match.group(1))
                log(f"  Día {day}: {plazas} prazas libres")
                return plazas
        page.wait_for_timeout(300)

    log(f"  Día {day}: no apareció el cuadro informativo de plazas tras el click")
    return None


def check_target_days(page, debug=False):
    """
    Devuelve dict {día: {"plazas_libres": int|None, "disponible": bool}}
    para TARGET_DAYS, haciendo click en cada día y leyendo el cuadro
    de "Prazas libres". Se considera disponible si plazas_libres es
    numérico y >= NUM_PRAZAS.
    """
    results = {}
    month_panel = find_month_container(page)
    scope = month_panel if month_panel is not None else page
    needed = int(NUM_PRAZAS)

    for day in TARGET_DAYS:
        try:
            plazas = read_plazas_for_day(page, day, scope, debug=debug)
        except Exception as e:
            log(f"  Día {day}: error al comprobar ({e})")
            plazas = None

        disponible = plazas is not None and plazas >= needed
        results[day] = {"plazas_libres": plazas, "disponible": disponible}
        page.wait_for_timeout(400)  # ser prudentes con el servidor

    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(DEBUG_DIR / "04_calendar.png"), full_page=True)
        (DEBUG_DIR / "04_calendar.html").write_text(page.content(), encoding="utf-8")

    return results


def load_previous_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def send_email(subject, body):
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    email_to = os.environ.get("EMAIL_TO")
    use_ssl = os.environ.get("SMTP_USE_SSL", "0").strip().lower() in {"1", "true", "yes", "si"}

    if not all([smtp_server, smtp_user, smtp_pass, email_to]):
        log("Faltan variables de entorno SMTP; no se envía email. Mensaje:")
        log(body)
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = email_to

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, [email_to], msg.as_string())
        else:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, [email_to], msg.as_string())
    except Exception as exc:
        log(f"Error al enviar el email: {exc}")
        return False

    log(f"Email enviado a {email_to}")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true",
                         help="Guarda capturas y HTML de cada paso en ./debug/")
    parser.add_argument("--test-email", action="store_true",
                         help="Envía un correo de prueba usando la configuración SMTP")
    args = parser.parse_args()

    if args.test_email:
        subject = "[Illas Cíes] Prueba de correo SMTP"
        body = "Este es un correo de prueba para comprobar que la configuración SMTP funciona."
        send_email(subject, body)
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            locale="gl-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        # Camufla el flag navigator.webdriver que delata a Playwright/Selenium
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        try:
            go_to_calendar(page, debug=args.inspect)
            results = check_target_days(page, debug=args.inspect)
        except (PWTimeout, RuntimeError) as e:
            log(f"ERROR durante la navegación: {e}")
            DEBUG_DIR.mkdir(exist_ok=True)
            try:
                page.screenshot(path=str(DEBUG_DIR / "error.png"), full_page=True)
                (DEBUG_DIR / "error.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
            browser.close()
            sys.exit(1)

        browser.close()

    if args.inspect:
        log("Modo --inspect terminado. Revisa la carpeta ./debug/")
        return

    available_now = {str(d) for d, info in results.items() if info["disponible"]}

    if available_now:
        lines = [
            f"  {d}/08/{TARGET_YEAR}: {results[int(d)]['plazas_libres']} prazas libres"
            for d in sorted(available_now, key=int)
        ]
        dates_str = ", ".join(sorted(available_now, key=int))
        first_day = sorted(available_now, key=int)[0]
        plazas = results[int(first_day)]["plazas_libres"]
        if plazas == 1:
            plural = "plaza libre"
        else:
            plural = "plazas libres"
        subject = f"[Illas Cíes] {plazas} {plural} detectadas para el día {first_day} de agosto"
        body = (
            f"Se ha detectado disponibilidad (>= {NUM_PRAZAS} plazas libres) para los "
            f"siguientes días de agosto:\n\n"
            + "\n".join(lines) + "\n\n" +
            f"Reserva aquí: {BASE_URL}\n"
            f"\n\nResultado completo de esta comprobación:\n"
            f"{json.dumps(results, indent=2, ensure_ascii=False)}\n\n"        
        )
        send_email(subject, body)
    else:
        log("No hay plazas libres en este momento; no se envía email.")

    save_state({
        "last_check": datetime.now().isoformat(timespec="seconds"),
        "available_days": sorted(available_now, key=int),
        "raw_results": {str(k): v for k, v in results.items()},
    })


if __name__ == "__main__":
    main()