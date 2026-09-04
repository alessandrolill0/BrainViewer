# TouchDesigner

Qui va il file `.toe` del progetto. È binario: Claude Code non può leggerlo. Per fargli
capire com'è fatta la rete di nodi, esporta la lista degli operatori in `exports/`.

## Metodo A — copia-incolla (10 secondi, nessuno script)

TouchDesigner mette sul clipboard la **definizione testuale** dei nodi selezionati.

1. Nella rete, seleziona i nodi (`Ctrl/Cmd + A` per tutti).
2. `Ctrl/Cmd + C`.
3. Incolla in un file di testo e salvalo come `exports/network.txt`.

Ottieni nomi, tipi, connessioni e valori dei parametri. È il modo più veloce e spesso
basta.

## Metodo B — script (CSV strutturato, con conteggio punti)

Utile per la nuvola di punti: dice quanti punti ha ogni SOP.

1. In TD apri il Textport: menu **Dialogs > Textport and DATs** (scorciatoia `Alt + T`).
2. Incolla questa riga e premi Invio:

```
exec(open('/Users/alessandrolillo/Developer/BrainViewer/touchdesigner/export_ops.py').read())
```

3. Deve stampare `Esportati N operatori in .../exports/operators.csv`.

Se dà errore `Nessun operatore a /project1`, la tua rete sta in un contenitore con un
altro nome. Scopri quale scrivendo nel Textport:

```
op('/').children
```

poi apri `export_ops.py` e cambia la variabile `START` con il percorso giusto.

## Metodo C — descrivimelo a parole

Se i primi due metodi ti fanno perdere tempo, va benissimo anche solo dirmi: come si
chiama il SOP che contiene la nuvola di punti, quanti punti ha, e come arriva al render.
Con quello posso già lavorare.

## Geometria

Per la segmentazione in lobi mi servirà anche la geometria vera e propria: da un SOP,
tasto destro > **Save Geometry** (o un *File Out SOP*) verso `exports/brain.obj`.
