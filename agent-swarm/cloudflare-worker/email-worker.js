/**
 * Cloudflare Email Worker — recibe TODO el correo entrante de tu dominio
 * (nesion.net) vía Cloudflare Email Routing, y lo reenvía al endpoint
 * /api/inbox/receive de tu panel para que quede asociado al agente
 * correcto (agente101@nesion.net -> agente número 101).
 *
 * Cloudflare Workers son JavaScript, no Python — es una exigencia de la
 * propia plataforma de Cloudflare, no hay forma de escribirlo en Python.
 * Es la única pieza de todo el proyecto que no está en Python.
 *
 * Instrucciones de despliegue: ver README, sección 15.
 */
export default {
  async email(message, env, ctx) {
    const rawBytes = await new Response(message.raw).arrayBuffer();
    const rawText = new TextDecoder().decode(rawBytes);

    // Extracción simple del cuerpo en texto plano. Suficiente para
    // encontrar enlaces y códigos de verificación.
    const bodyMatch = rawText.split(/\r?\n\r?\n/).slice(1).join("\n\n");
    const text = bodyMatch || rawText;

    const payload = {
      to: message.to,
      from: message.from,
      subject: message.headers.get("subject") || "",
      text: text.slice(0, 5000),
    };

    await fetch(env.DASHBOARD_INBOX_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Inbox-Secret": env.INBOX_WEBHOOK_SECRET,
      },
      body: JSON.stringify(payload),
    });
  },
};
