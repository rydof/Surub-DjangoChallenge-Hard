## Pornire

```bash
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py runserver
```

Deschide **http://127.0.0.1:8000**. Celery pornește automat cu serverul.

## Pagini

- **`/unit`** — adaugă un luptător.
- **`/unit/all`** — lista luptătorilor; selectează doi din echipe diferite și apasă **Simulate**. Butonul **Heal** apare pentru luptătorii răniți. Statisticile echipelor sunt sub listă.
- **`/unit/<id>`** — fișa luptătorului și istoricul luptelor.
- **`/people`** — descarcă și înrolează cetățenii în loturi paralele (**Run Full Parallel Sync**).
- **`/simulate`** — apasă **Fetch `/fight/<batch>`** ca să descarci loturile de lupte. Apoi apasă **Replay Pending Fights in Parallel** ca să ruleze fiecare luptă în paralel și să vezi rezultatele. **Clear Fight History** șterge istoricul și readuce toți luptătorii la 100 HP.
