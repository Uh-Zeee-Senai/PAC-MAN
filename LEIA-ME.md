# PAC-MAN — Python + Arduino

O jogo roda no **notebook com Pygame**. O Arduino atua como controle físico: lê o joystick (e botão embutido) e recebe do computador o número de vidas para acender os 3 LEDs. Não precisa de display, Wi-Fi, biblioteca Arduino ou fonte externa; basta um cabo USB de dados.

## Materiais
- 1 Arduino Uno ou Nano compatível
- 1 módulo joystick analógico de 5 pinos (GND, +5V, VRx, VRy, SW)
- 3 LEDs, de preferência vermelhos, e **3 resistores de 220 a 330 ohms**
- Protoboard, jumpers e cabo USB de dados
- Notebook com Python e Arduino IDE

## Ligação do joystick

| Joystick | Arduino |
|---|---|
| GND | GND |
| +5V / VCC | 5V |
| VRx / X | A0 |
| VRy / Y | A1 |
| SW | D2 |

> Atenção: alguns módulos usam ordem física de pinos diferente. Confira **o nome impresso no módulo**, não a posição.

## Ligação dos LEDs

| LED | Arduino | Circuito |
|---|---|---|
| Vida 1 | D8 | D8 → resistor 220–330 Ω → anodo (+, perna longa) → catodo (−, perna curta) → GND |
| Vida 2 | D9 | D9 → resistor 220–330 Ω → anodo (+) → catodo (−) → GND |
| Vida 3 | D10 | D10 → resistor 220–330 Ω → anodo (+) → catodo (−) → GND |

Os 3 LEDs podem compartilhar o mesmo **GND**, mas cada LED precisa do seu próprio resistor. **Nunca** ligue um LED diretamente ao pino sem resistor.

## Preparação (Windows)

1. Instale Python (marque a opção **Add Python to PATH**) e Arduino IDE.
2. Crie a pasta e mantenha os arquivos `pacman.py`, `controle_pacman.ino`, `requirements.txt` nela.
3. Conecte o Arduino por USB, abra `controle_pacman.ino` na Arduino IDE.
4. Selecione **Ferramentas → Placa** e **Ferramentas → Porta**; envie com **Carregar/Upload**.
5. **Feche o Monitor Serial** da Arduino IDE antes de executar Python (a porta só pode ser usada por um programa de cada vez).
6. Abra o Terminal/PowerShell dentro da pasta, execute:

```powershell
py -m pip install -r requirements.txt
py pacman.py
```

Se não detectar a placa, descubra a COM:

```powershell
py pacman.py --list-ports
py pacman.py --port COM3
```

Troque `COM3` pela porta real (por exemplo `COM4`, `COM5`). Também é possível verificar no **Gerenciador de Dispositivos → Portas (COM e LPT)**. Sem Arduino, ainda é possível jogar pelo teclado.

## Controles

- **Joystick:** mover o Pac-Man pelas quatro direções.
- **Botão SW do joystick:** iniciar, pausar/continuar, reiniciar após derrota ou passar à próxima fase.
- **Teclado (reserva):** setas ou WASD para mover; Espaço/Enter para iniciar/pausar; R para reconectar USB; Esc para fechar.
- Com 3 vidas: 3 LEDs acesos; com 2: 2 acesos; com 1: 1 aceso; com 0: nenhum LED aceso.

## Jogo

Colete os pontos no labirinto, fuja dos três fantasmas e use as bolinhas especiais rosas para ganhar alguns segundos de poder e comer fantasmas. Ao limpar o labirinto, avance para outra fase; o jogo aumenta a dificuldade. As vidas permanecem sincronizadas com os LEDs, inclusive após recomeçar.

## Solução de problemas

- **A janela abre, mas o Arduino não responde:** confirme que o código foi enviado, feche o Monitor Serial, veja a porta com `--list-ports` e execute `--port COMx`. Aperte **R** para tentar reconectar.
- **Controle anda ao contrário:** edite `INVERTER_X = True` e/ou `INVERTER_Y = True` no topo de `pacman.py`, dependendo do eixo invertido.
- **Controle mexe sozinho:** ajuste `LIMIAR_JOYSTICK` (padrão 175) ou confira se VRx/VRy estão ligados corretamente.
- **Erro No module named pygame/serial:** instale as bibliotecas com `py -m pip install -r requirements.txt` usando o mesmo Python que executa o jogo.
- **Access is denied / Permission denied:** feche a IDE/Monitor Serial ou outro programa que esteja ocupando a mesma porta.
- **LED não acende:** confira polaridade, resistor, GND comum e a ligação ao pino D8/D9/D10.
- **Arduino não aparece em Portas:** confira se o cabo USB é de dados; placas compatíveis podem exigir driver USB (CH340/CP210x, conforme o chip USB da placa).

## Protocolo de comunicação

Usa USB serial a **115200 bauds** e mensagens de texto terminadas em `\n`:

- Arduino → Python: `J,510,520,0` (X, Y, botão; 1 = pressionado).
- Python → Arduino: `V:2` (acender exatamente dois LEDs).

## Notas

Projeto didático para Arduino Uno/Nano de 5 V. Em placas que trabalham em 3,3 V, confirme a tensão e os pinos antes de conectar o joystick. O Arduino não executa o jogo nem precisa armazenar imagens: toda a renderização e lógica acontecem no notebook.

## Teste elétrico antes de jogar (opcional, recomendado)

Depois de enviar `controle_pacman.ino` para a placa e **fechar o Monitor Serial**, execute:

```powershell
py diagnostico.py --port COM3
```

O diagnóstico imprime os valores de X/Y (aproximadamente 512 no centro), detecta o botão e percorre a sequência de 3, 2, 1, 0 e 3 LEDs acesos. No Windows, substitua `COM3` pela porta encontrada na IDE. Feche o diagnóstico antes de iniciar `pacman.py`.
