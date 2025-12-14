# Migration notes: LO100 -> Proxmox VM control

Tässä versiossa LLM-agent ei enää käynnistä/sammuta koko rautapalvelinta (LO100),
vaan ohjaa **Proxmoxissa pyöriviä VM:iä** Proxmox VE REST API:n kautta.

## Tarvitset

- Proxmox API token (Datacenter -> Permissions -> API Tokens)
- LLM-VM:n VMID (qemu)
- Windows-VM:n VMID (qemu)
- (Valinnainen) iLO/IPMI tunnukset jos haluat nähdä hostin health/CPU temp UI:ssa

## Pakolliset ympäristömuuttujat (systemd service)

PROXMOX_TOKEN_ID="user@realm!tokenid"
PROXMOX_TOKEN_SECRET="...token secret..."
LLM_VM_ID="123"
WINDOWS_VM_ID="124"

## Suositellut

PROXMOX_HOST="192.168.8.31"
LLM_HOST="192.168.8.33"

## Huoltotila

Huoltotila estää:
- automaattisen LLM-VM:n käynnistyksen (chat/wake)
- idle-shutdown automaation

UI:ssa on nappi huoltotilan togglaamiseen.

Huoltotila tallennetaan state-fileen:
  /var/lib/llm-agent/state.json
polkua voi muuttaa env:llä AGENT_STATE_FILE.

## iLO/IPMI (valinnainen)

Aseta:
  ILO_IP, ILO_USER, ILO_PASS

Jos et aseta, UI näyttää health/CPU temp 'tuntematon'.
