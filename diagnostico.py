"""Teste rápido do joystick e dos 3 LEDs, sem abrir o jogo.

Uso: python diagnostico.py --port COM3
O teste mostra X, Y, botão e apaga um LED a cada 3 segundos.
"""
import argparse
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    raise SystemExit("Instale pyserial: python -m pip install pyserial")


def escolher_porta(porta):
    if porta:
        return porta
    portas = list(list_ports.comports())
    candidatas = [p.device for p in portas
                  if any(marca in (p.device + ' ' + p.description).lower()
                         for marca in ('arduino', 'ch340', 'ch341', 'usb serial',
                                       'usb-serial', 'cp210', 'ttyacm', 'ttyusb'))]
    if len(candidatas) == 1:
        return candidatas[0]
    if not candidatas and len(portas) == 1:
        return portas[0].device
    opcoes = ', '.join(f'{p.device} ({p.description})' for p in portas)
    raise SystemExit(f"Informe a porta com --port COMx. Portas encontradas: {opcoes or 'nenhuma'}")


def main():
    parser = argparse.ArgumentParser(description='Diagnóstico da montagem do Pac-Man Arduino')
    parser.add_argument('--port', help='Ex.: COM3')
    args = parser.parse_args()
    porta = escolher_porta(args.port)
    print(f'Conectando à porta {porta} em 115200...')
    try:
        with serial.Serial(porta, 115200, timeout=0.4, write_timeout=1) as arduino:
            time.sleep(2.0)  # aguarda o reinício automático de Uno/Nano
            print('Mova o joystick e pressione o botão. Teste de LEDs: 3, 2, 1, 0, 3.')
            sequencia = [3, 2, 1, 0, 3]
            for vidas in sequencia:
                arduino.write(f'V:{vidas}\n'.encode('ascii'))
                print(f'\nLEDs esperados acesos: {vidas}')
                fim = time.monotonic() + 3.0
                ultima_exibicao = 0.0
                while time.monotonic() < fim:
                    linha = arduino.readline().decode('ascii', errors='ignore').strip()
                    campos = linha.split(',')
                    if len(campos) == 4 and campos[0] == 'J':
                        try:
                            x, y, botao = (int(v) for v in campos[1:])
                        except ValueError:
                            continue
                        agora = time.monotonic()
                        if agora - ultima_exibicao > 0.25:
                            print(f'X = {x:4d} | Y = {y:4d} | Botão = {"PRESSIONADO" if botao else "solto"}')
                            ultima_exibicao = agora
            print('\nTeste encerrado! Feche esta janela antes de iniciar o jogo.')
    except (serial.SerialException, OSError) as exc:
        raise SystemExit(f'Erro de conexão: {exc}. Feche o Monitor Serial e confira a porta USB.')


if __name__ == '__main__':
    main()
