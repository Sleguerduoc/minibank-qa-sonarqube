import { useEffect, useRef, useState } from 'react'
import { llamar, pesos } from './api.js'

// Canal restringido al mismo origen: reemplaza postMessage(..., '*') y window.opener.
const CANAL = 'minibank-documentos'

export function esVistaDocumento() {
  return new URLSearchParams(window.location.search).get('vista') === 'documento'
}

function crearPin() {
  const valor = new Uint32Array(1)
  window.crypto.getRandomValues(valor)
  return String(100000 + (valor[0] % 900000))
}

export default function Documentos({ token, sesionInvalida }) {
  const vistaDocumento = esVistaDocumento()
  const [formato, setFormato] = useState('actual')
  const [documento, setDocumento] = useState(null)
  const [resultado, setResultado] = useState(null)
  const [busy, setBusy] = useState(false)
  const documentoActual = useRef(null)

  useEffect(() => {
    const canal = new BroadcastChannel(CANAL)
    canal.onmessage = (event) => {
      const mensaje = event.data
      if (!mensaje || typeof mensaje !== 'object') return
      if (!vistaDocumento && mensaje.tipo === 'minibank-documento-listo' && documentoActual.current) {
        canal.postMessage({ tipo: 'minibank-documento', datos: documentoActual.current })
      }
      if (vistaDocumento && mensaje.tipo === 'minibank-documento' && mensaje.datos?.documento) {
        documentoActual.current = mensaje.datos
        setDocumento(mensaje.datos)
        setResultado({ ok: true, mensaje: 'Documento recibido.' })
      }
    }
    if (vistaDocumento) canal.postMessage({ tipo: 'minibank-documento-listo' })
    return () => canal.close()
  }, [vistaDocumento])

  async function preparar(e) {
    e.preventDefault(); setBusy(true); setResultado(null)
    const { res, data } = await llamar('/lab/documentos', {
      metodo: 'POST', token,
      cuerpo: { formato, pin_cliente: crearPin() },
    })
    setBusy(false)
    if (res.status === 401) return sesionInvalida(token)
    if (!res.ok) {
      return setResultado({ ok: false, mensaje: data.mensaje || 'No fue posible preparar el documento. Revise la conexión.' })
    }
    documentoActual.current = data
    setDocumento(data)
    setResultado({ ok: true, mensaje: 'Documento preparado con el estado actual de la cuenta.' })
  }

  function abrir() {
    // noopener + noreferrer: la ventana nueva no obtiene referencia a esta (window.opener = null).
    window.open(new URL('?vista=documento', window.location.href).href, '_blank', 'noopener,noreferrer')
    setResultado({ ok: true, mensaje: 'Documento abierto en una nueva ventana.' })
  }

  return <section className="tarjeta documentos">
    <h2>{vistaDocumento ? 'Documento de la cuenta' : 'Documentos y autorizaciones'}</h2>
    {!vistaDocumento && <form className="formulario-documentos" onSubmit={preparar}>
      <label>Formato de exportación<select value={formato} onChange={e => setFormato(e.target.value)}>
        <option value="actual">Actual</option>
        <option value="archivo">Archivo</option>
        <option value="legacy">Compatibilidad</option>
      </select></label>
      <button disabled={busy}>{busy ? 'Preparando…' : 'Preparar documento'}</button>
      <button type="button" className="secundario" disabled={!documento || busy} onClick={abrir}>Ver documento en otra ventana</button>
    </form>}
    {resultado && <p role="status" className={'aviso ' + (resultado.ok ? 'ok' : 'error')}>{resultado.mensaje}</p>}
    {vistaDocumento && !documento && <p role="status">Esperando el documento de la ventana principal…</p>}
    {documento && <>
      <dl className="datos-documento">
        <div><dt>Documento</dt><dd>{documento.documento_id}</dd></div>
        <div><dt>Titular</dt><dd>{documento.documento.titular}</dd></div>
        <div><dt>RUT</dt><dd>{documento.documento.rut}</dd></div>
        <div><dt>Saldo</dt><dd>{pesos(documento.documento.saldo)}</dd></div>
        <div><dt>Transferido hoy</dt><dd>{pesos(documento.documento.transferido_hoy)}</dd></div>
        <div><dt>PIN de autorización</dt><dd>{documento.pin_cliente}</dd></div>
        <div><dt>Código del documento</dt><dd>{documento.codigo_autorizacion}</dd></div>
      </dl>
      <p className="nota">Movimientos incluidos: {documento.documento.movimientos?.length || 0}. Datos de práctica tomados al preparar el documento.</p>
      {vistaDocumento && <button className="secundario" onClick={() => window.close()}>Cerrar documento</button>}
    </>}
  </section>
}
