# Laboratorio 2: Esquemas de detección y corrección de errores

**CURSO:** CC3067 Redes

## Integrantes

- Dulce Ambrosio - 231143
- Juan Cruz - 23110 

## Descripción

Simulación de la transmisión de un mensaje entre dos programas escritos en
lenguajes distintos, aplicando ruido al canal y verificando la integridad de la
trama en el receptor.

- Emisor: Python
- Receptor: C++
- Algoritmo de corrección: código de Hamming, para cualquier código (n, m) que
  cumpla `(m + r + 1) <= 2^r`. El valor de `m` se elige al enviar y `r` se
  calcula solo.
- Algoritmo de detección: CRC-32 con el polinomio estándar de IEEE 802.3
  (`0xEDB88320` reflejado, init y xorout `0xFFFFFFFF`).

## Capas

```
EMISOR (Python)                                 RECEPTOR (C++)

APLICACION    solicitar_mensaje                 mostrar_mensaje
PRESENTACION  codificar_mensaje                 decodificar_mensaje
ENLACE        calcular_integridad               verificar_integridad, corregir_mensaje
RUIDO         aplicar_ruido
TRANSMISION   enviar_informacion  --socket-->   recibir_informacion
```

## Estructura

```
emisor/
  emisor.py          programa principal del emisor
  hamming.py         codificación de Hamming
  crc32_utils.py     cálculo del CRC-32
receptor/
  receptor.cpp       programa principal del receptor
  hamming.h/.cpp     verificación y corrección de Hamming
  crc32_utils.h/.cpp verificación del CRC-32
  Makefile
pruebas.py           corre las simulaciones y genera las gráficas
resultados/          gráficas y CSV generados por pruebas.py
```

## Compilar el receptor

```bash
cd receptor
make
```

En Windows hay que usar una consola MSYS2 (UCRT64 o MinGW64) con `gcc` y `make`
instalados (`pacman -S --needed mingw-w64-ucrt-x86_64-gcc make`), porque el
código usa sockets y el Makefile enlaza `-lws2_32`. Desde PowerShell solo
funciona si `g++` y `make` están en el PATH.

## Uso con sockets

Primero se levanta el receptor, que se queda escuchando:

```bash
cd receptor
./receptor 5000
```

Y en otra terminal se corre el emisor:

```bash
cd emisor
python3 emisor.py --host 127.0.0.1 --port 5000
```

Sin argumentos pide todo de forma interactiva: el mensaje (como texto o como
binario), el algoritmo, el valor de `m` si es Hamming, y la tasa de error.
También se puede pasar todo por línea de comandos:

```bash
python3 emisor.py --mensaje "Hola Mundo" --algoritmo crc32 --tasa-error 0
python3 emisor.py --mensaje "Hola Mundo" --algoritmo hamming --m 4 --tasa-error 1/50
python3 emisor.py --binario 0100100001101001 --algoritmo hamming --m 4 --tasa-error 0
```

Opciones del emisor:

- `--mensaje`: texto a enviar.
- `--binario`: mensaje ya en binario, en lugar de texto.
- `--algoritmo`: `hamming` o `crc32`.
- `--m`: bits de datos por bloque de Hamming (por defecto 4, o sea Hamming(7,4)).
- `--tasa-error`: probabilidad de error por bit, en formato `1/N`.
- `--forzar-flips`: posiciones exactas de bits a invertir, separadas por coma.
- `--host` y `--port`: dirección del receptor.
- `--manual`: no usa sockets, solo imprime la trama.

## Uso sin sockets

Para pasar la trama a mano, el emisor se corre con `--manual` y el receptor con
`--stdin`:

```bash
python3 emisor.py --mensaje "Hola" --algoritmo hamming --m 4 --forzar-flips 2 --manual
```

Eso imprime dos líneas (el header y la trama) que se pegan en el receptor:

```bash
./receptor --stdin
```

`--forzar-flips` sirve para reproducir siempre el mismo error: las posiciones
son 0-indexadas sobre la trama ya codificada, incluyendo los bits de
redundancia.

## Protocolo

Se mandan dos líneas de texto:

```
<HEADER>
<BITS>
```

El header es `HAMMING:<m>:<r>:<bits_originales>` o `CRC32:<bits_de_datos>`, y
sirve para que el receptor sepa qué algoritmo usar y dónde termina la
información útil.

## Resultados en el receptor

- Sin errores: muestra el mensaje decodificado.
- Errores corregidos: solo con Hamming, indica en cuántos bloques hubo error y
  muestra el mensaje recuperado.
- Trama descartada: con CRC-32 cuando el checksum no coincide, y con Hamming
  cuando el síndrome apunta fuera del bloque o la trama no tiene un número
  entero de bloques.

Hamming corrige un error por bloque. Si caen dos errores en el mismo bloque, el
síndrome apunta a un tercer bit y el receptor "corrige" el bit equivocado, así
que entrega un mensaje dañado creyendo que está bien. Se puede reproducir con:

```bash
python3 emisor.py --mensaje "Hola" --algoritmo hamming --m 4 --forzar-flips 2,5 --manual
```

## Pruebas y gráficas

```bash
python3 pruebas.py
```

Genera 100 transmisiones por cada combinación de algoritmo, longitud de mensaje
(4 a 64 caracteres) y tasa de error (de 0 a 1/10), pasando todas las tramas por
el receptor de C++ real. Deja en `resultados/` un CSV con los datos y cuatro
gráficas: overhead, mensajes entregados correctamente, errores no detectados y
efecto de la longitud del mensaje. Requiere matplotlib.
