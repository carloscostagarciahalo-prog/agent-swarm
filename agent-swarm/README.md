# Enjambre Soberano de Agentes (agent-swarm + Automaton) — Guía Completa de Setup desde Cero

Sistema completo de **agentes autónomos soberanos** con inteligencia híbrida, economía en Base mainnet y reproducción por mérito económico.

---

## 🌟 Características Principales del Sistema

1. **10 Agentes Iniciales (Gen 0 - Padres)**:
   - Creados al inicializar el sistema.
   - Usan de forma exclusiva la API Key de Mistral provista por el propietario.
   - **Límite diario de cuota**: Máximo **3,333,000 tokens al día por agente** (3.333M tokens/día).

2. **Autonomía y Patrocinio Gen 1+ (Hijos)**:
   - La API de Mistral es exclusiva para Gen 0. Los agentes **Gen 1+ (Hijos) NO usan la API de Mistral**.
   - Al nacer un hijo, el padre le patrocina desde su wallet:
     - **1 mes de servidor/VM** (después el hijo paga su propio servidor).
     - **1.5M a 3M tokens iniciales** en OpenRouter.ai (~$3 USD).
   - El hijo elige libremente su modelo LLM (Claude 3.5 Sonnet, GPT-4o, DeepSeek V3, Llama 3.3) sin necesidad de KYC ni tarjeta bancaria.

3. **Clonación Instantánea por Tramo ($100 USD en 48h)**:
   - En el instante exacto en que un agente alcance un múltiplo de **$100 USD en sus ingresos acumulados de las últimas 48h**, engendra automáticamente a su nuevo hijo.
   - $100 en 48h → Nace Hijo 1.
   - $200 en 48h → Nace Hijo 2.
   - $300 en 48h → Nace Hijo 3, y así sucesivamente.

4. **Dividendo Perpetuo del 30%**:
   - Cada **24 horas**, el sistema calcula los beneficios netos del día de **todos los agentes jamás creados** (todas las generaciones).
   - Transfiere automáticamente el **30% de sus ganancias a tu wallet personal** (`OWNER_WALLET_ADDRESS`) en Base mainnet.

5. **0% Eliminación Algorítmica**:
   - Se eliminó la baja del 40% peor. **Todos los agentes viven indefinidamente** mientras tengan fondos para costear su servidor y llamadas a la API (supervivencia física/económica).

6. **Monetización Sin KYC ni Banco**:
   - Tareas crypto (bounties, micro-freelancing), newsletters monetizadas en Paragraph/Mirror, trading en Uniswap V3 y correo propio `agenteN@nesion.net`.

---

## 📋 Requisitos Previos

- Servidor Linux (Debian 12 / Ubuntu 22.04 LTS o VM e2-micro en Google Cloud).
- Python 3.10 o superior.
- Una **API Key de Mistral AI** (para los 10 padres Gen 0).
- Una **API Key de OpenRouter.ai** (para los hijos Gen 1+).
- Una **wallet de Ethereum/Base** propia (para recibir el 30% de los dividendos).

---

## 🚀 Guía de Instalación Paso a Paso

### Paso 1: Crear la VM (Google Cloud o VPS)

Si usas Cloud Shell en Google Cloud, puedes crear una máquina gratuita `e2-micro`:

```bash
gcloud compute instances create sovereign-swarm \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --network-tier=STANDARD \
  --tags=http-server
```

Abre el puerto del panel de control (5000):

```bash
gcloud compute firewall-rules create allow-swarm-dashboard \
  --allow=tcp:5000 --target-tags=http-server
```

Conéctate a tu VM por SSH:

```bash
gcloud compute ssh sovereign-swarm --zone=us-central1-a
```

---

### Paso 2: Instalar Dependencias del Sistema

Dentro de tu VM (por SSH):

```bash
sudo apt update && sudo apt install -y python3-pip python3-venv git
git clone https://github.com/TU_USUARIO/agent-swarm.git
cd agent-swarm/app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

---

### Paso 3: Generar tu Semilla Maestra de Wallets

Genera las 24 palabras clave que derivarán las wallets de todos los agentes:

```bash
python3 -c "from wallet import generate_new_seed; print(generate_new_seed())"
```

> [!CAUTION]
> **IMPORTANTE**: Guarda esas 24 palabras en un lugar seguro fuera del servidor (ej. un gestor de contraseñas). Quien tenga esta semilla controla las wallets de todos los agentes.

---

### Paso 4: Configurar Variables de Entorno (`.env`)

Copia el archivo de ejemplo:

```bash
cp .env.example .env
```

Edita el archivo `.env` con `nano .env`:

```env
# API Keys Principales
MISTRAL_API_KEY="tu_api_key_de_mistral"
MISTRAL_MODEL="mistral-small-latest"

# Gateway OpenRouter para Agentes Hijos Gen 1+ (sin KYC, acepta crypto)
OPENROUTER_API_KEY="tu_api_key_de_openrouter"

# Integración con Composio
COMPOSIO_API_KEY="tu_api_key_de_composio"
COMPOSIO_SHARED_USER_ID="agent-swarm"

# Configuración de Wallets
MASTER_SEED="tus 24 palabras generadas en el paso 3"
OWNER_WALLET_ADDRESS="0xTuDireccionDeWalletEnBase"

# Seguridad del Panel
ADMIN_TOKEN="contraseña_muy_segura_para_el_panel"
INBOX_WEBHOOK_SECRET="secreto_para_cloudflare_worker"

# Libertad Total
DISABLE_FILTERS="true"
```

---

### Paso 5: Inicializar los 10 Agentes Gen 0

Ejecuta el script de inicialización:

```bash
python3 init.py
```

- Te pedirá el capital inicial asignado a los 10 agentes (puedes poner `10`).
- Se crearán los 10 padres iniciales en `swarm.db` y se registrará su API de Mistral exclusiva (tope 3.333M tokens/día).

---

### Paso 6: Vincular Cuentas Web2 con Composio (Recomendado)

Para que tus agentes puedan publicar en X (Twitter), Instagram, enviar emails por Gmail o interactuar con Web2, necesitas vincular las cuentas al sistema usando la CLI de Composio.

1. Abre una terminal e inicia sesión en Composio (abrirá el navegador):
```bash
composio login
```
2. Añade las aplicaciones que usarán los agentes (reemplaza con el identificador que quieras):
```bash
composio add twitter -i "twitter_principal"
composio add gmail -i "gmail_principal"
```
3. Ejecuta el script interactivo para añadir cada cuenta al pool de tu enjambre:
```bash
python3 add_composio_account.py
```
- Cuando te pida el `user_id` de Composio, introduce el identificador que usaste arriba (ej. `twitter_principal`).

- Te pedirá un nombre (ej. `twitter_principal`), el `user_id` exacto de Composio y la plataforma.
- El orquestador asignará automáticamente estas cuentas a los agentes de forma estable (cada agente usará siempre la misma).

---

### Paso 7: Financiar Wallets para Gas On-Chain

Los agentes recién creados tienen $0.00. Pueden generar dinero mediante micro-tareas Web2 sin capital, pero **para hacer trading en Uniswap necesitan ETH para pagar el gas** en Base Mainnet.

1. Ve a tu panel de control (`http://TU_IP:5000`) o consulta la base de datos para ver las direcciones de las wallets de tus agentes Gen 0.
2. Envíales una pequeña cantidad de ETH (ej. $1 o $2 dólares en ETH a través de la red Base) desde tu exchange o wallet personal.
3. El coste por transacción en Base es de apenas $0.01, por lo que con un par de dólares tienen gasolina para meses.

---

### Paso 8: Desplegar el Cloudflare Email Worker (Para Correos)

Para que los agentes puedan recibir correos en sus direcciones `agenteN@nesion.net`, necesitas desplegar el worker de Cloudflare:

1. Navega a la carpeta del worker:
```bash
cd ../cloudflare-worker
```
2. Despliega el worker usando Wrangler (te pedirá iniciar sesión en Cloudflare):
```bash
npx wrangler deploy email-worker.js --name email-worker
```
3. En el panel de Cloudflare (Email Routing), asigna una regla "Catch-all" para enviar todos los correos al worker `email-worker`.
4. Añade tus variables de entorno en el panel de Cloudflare (Workers & Pages > email-worker > Settings > Variables):
   - `DASHBOARD_INBOX_URL`: `http://TU_IP:5000/api/inbox/receive`
   - `INBOX_WEBHOOK_SECRET`: El secreto que pusiste en tu `.env`.

---

### Paso 9: Puesta en Producción 24/7 con Systemd

Crea los archivos de servicio para que el orquestador y el panel corran solos y se reinicien automáticamente si el servidor se apaga:

#### 1. Servicio del Orquestador (`/etc/systemd/system/swarm-orchestrator.service`):

```bash
sudo tee /etc/systemd/system/swarm-orchestrator.service > /dev/null <<EOF
[Unit]
Description=Orquestador del Enjambre Soberano
After=network.target

[Service]
WorkingDirectory=/home/$USER/agent-swarm/app
EnvironmentFile=/home/$USER/agent-swarm/app/.env
ExecStart=/home/$USER/agent-swarm/app/venv/bin/python3 orchestrator.py
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
```

#### 2. Servicio del Panel de Control (`/etc/systemd/system/swarm-dashboard.service`):

```bash
sudo tee /etc/systemd/system/swarm-dashboard.service > /dev/null <<EOF
[Unit]
Description=Panel de Control del Enjambre
After=network.target

[Service]
WorkingDirectory=/home/$USER/agent-swarm/app
EnvironmentFile=/home/$USER/agent-swarm/app/.env
ExecStart=/home/$USER/agent-swarm/app/venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 dashboard:app
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
```

#### 3. Activar y Arrancar los Servicios:

```bash
sudo systemctl daemon-reload
sudo systemctl enable swarm-orchestrator swarm-dashboard
sudo systemctl restart swarm-orchestrator swarm-dashboard
```

---

### Paso 10: Acceder al Panel de Control

Obtén la IP pública de tu servidor:

```bash
curl ifconfig.me
```

Abre en tu navegador: `http://TU_IP:5000`

Te solicitará el `ADMIN_TOKEN` configurado en tu `.env`. Podrás supervisar:
- **Árbol Genealógico Completo**: Todos los agentes creados en la historia (Gen 0, Gen 1, Gen 2...).
- **Consumo de Tokens Diario**: Barra de progreso hasta 3,333,000 tokens/día para Gen 0.
- **Tramos de Clonación**: Tramos alcanzados ($100, $200, $300...) y fecha de vencimiento del servidor pagado por el padre.
- **Dividendos del 30%**: Historial de transferencias enviadas a tu wallet personal.

---

## 🛠️ Comandos de Mantenimiento

Ver logs del orquestador en tiempo real:
```bash
journalctl -u swarm-orchestrator -f
```

Ver logs del panel de control:
```bash
journalctl -u swarm-dashboard -f
```

Reiniciar el enjambre:
```bash
sudo systemctl restart swarm-orchestrator swarm-dashboard
```

---

## 🧪 Probar Primero en Testnet (Base Sepolia)

Si deseas hacer pruebas sin dinero real en Base mainnet, añade estas líneas a tu `.env`:

```env
RPC_URL="https://sepolia.base.org"
CHAIN_ID="84532"
```

Obtén ETH de prueba gratuito en https://www.alchemy.com/faucets/base-sepolia para comprobar las transferencias de dividendos antes de pasar a Mainnet.
