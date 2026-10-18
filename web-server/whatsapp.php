<?php
// whatsapp.php - Primary Group Monitoring & AI Assistant Dashboard
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WhatsApp Group Monitor Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen flex flex-col">
    <!-- Header -->
    <header class="bg-slate-800 border-b border-slate-700 px-6 py-4 flex justify-between items-center">
        <h1 class="text-xl font-bold text-emerald-400 flex items-center gap-2">
            <span>WhatsApp Group Monitor & AI Hub</span>
        </h1>
        <div class="flex items-center gap-4 text-sm text-slate-400">
            <a href="whatsapp.php" class="text-emerald-400 font-medium">Dashboard</a>
            <a href="email-reports.php" class="hover:text-slate-200 transition">Email Reports</a>
        </div>
    </header>

    <!-- Main Layout -->
    <main class="flex-1 flex flex-col md:flex-row overflow-hidden">
        <!-- Sidebar: Groups List -->
        <aside class="w-full md:w-1/3 lg:w-1/4 bg-slate-800/50 border-r border-slate-700 p-4 flex flex-col">
            <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">Monitored Groups</h2>
            <div id="groups-list" class="flex-1 overflow-y-auto space-y-2 pr-1">
                <p class="text-slate-500 text-sm italic">Loading groups...</p>
            </div>
        </aside>

        <!-- Content Area: Chat & AI Assistant -->
        <section class="flex-1 flex flex-col bg-slate-900 p-6 overflow-hidden">
            <div class="mb-4 flex justify-between items-center">
                <h2 id="selected-group-title" class="text-lg font-semibold text-slate-200">Select a group to begin</h2>
                <span id="group-status" class="text-xs px-2.5 py-1 rounded-full bg-slate-800 text-slate-400">Idle</span>
            </div>

            <!-- Chat / Message Viewport -->
            <div id="chat-viewport" class="flex-1 bg-slate-800/40 border border-slate-700 rounded-xl p-4 overflow-y-auto space-y-4 mb-4 flex flex-col justify-end">
                <div class="text-center text-slate-500 my-auto">
                    <p>Select a WhatsApp group from the sidebar or ask the AI assistant a question below.</p>
                </div>
            </div>

            <!-- AI Ask Input Box -->
            <div class="bg-slate-800 border border-slate-700 rounded-xl p-3 flex gap-3 items-center">
                <input type="text" id="ai-prompt-input" placeholder="Ask AI about group insights or messages..." class="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-emerald-500">
                <button id="send-ai-btn" class="bg-emerald-600 hover:bg-emerald-500 text-white px-5 py-2.5 rounded-lg text-sm font-medium transition flex items-center gap-2">
                    <span>Ask AI</span>
                </button>
            </div>
        </section>
    </main>

    <script>
        // Fetch groups on Loading
        async function loadGroups(){
            try{
                const res = await fetch('whatsapp-data.php?endpoint=/groups');
                const groups = await res.json();
                const container = document.getElementById('groups-list');

                if (!groups || groups.length === 0) {
                    container.innerHTML = '<p class="text-slate-500 text-sm italic">No groups found.</p>';
                    return;
                }

                container.innerHTML = groups.map(g => {
                    const jid = g.jid || g.id;
                    const name = g.name || 'Group';
                    return `
                        <div data-jid="${escapeHtml(jid)}" data-name="${escapeHtml(name)}" class="group-card p-3 bg-slate-800 hover:bg-slate-700/70 border border-slate-700/50 rounded-xl cursor-pointer transition">
                            <h3 class="font-medium text-slate-200 truncate">${escapeHtml(name)}</h3>
                            <p class="text-xs text-slate-400 mt-1">Last active: ${g.updated_at ? new Date(g.updated_at).toLocaleString() : 'N/A'}</p>
                        </div>
                    `;
                }).join('');

                // Attach a clean event listener to handle clicks without quote/apostrophe issues
                container.querySelectorAll('.group-card').forEach(card => {
                    card.addEventListener('click', () => {
                        selectGroup(card.dataset.jid, card.dataset.name);
                    });
                });
            } catch(err){
                console.error('Failed to load groups:', err);
                document.getElementById('groups-list').innerHTML = '<p class="text-rose-400 text-sm">Error loading groups.</p>';
            }
        }

        let currentGroupJid = null;

        async function selectGroup(jid, name){
            currentGroupJid = jid;
            document.getElementById('selected-group-title').textContent = name;
            document.getElementById('group-status').textContent = 'Active';
            document.getElementById('group-status').className = 'text-xs px-2.5 py-1 rounded-full bg-emerald-900/50 text-emerald-400 border border-emerald-700/50';

            const viewport = document.getElementById('chat-viewport');
            viewport.innerHTML = '<p class="text-slate-400 text-sm text-center my-auto">Loading messages...</p>';

            try{
                const res = await fetch(`whatsapp-data.php?endpoint=/messages&jid=${encodeURIComponent(jid)}`);
                const messages = await res.json();

                if (!messages || messages.length === 0) {
                    viewport.innerHTML = `
                        <div class="text-center text-slate-500 my-auto">
                            <p>No messages recorded yet for <strong>${escapeHtml(name)}</strong>. You can ask the AI assistant questions about this group below.</p>
                        </div>
                    `;
                    return;
                }

                viewport.innerHTML = messages.map(m => `
                    <div class="flex flex-col items-start">
                        <div class="max-w-[80%] bg-slate-800 border border-slate-700 text-slate-100 rounded-2xl rounded-tl-sm px-4 py-2.5 text-sm">
                            <span class="block text-xs text-emerald-400 font-medium mb-1">${escapeHtml(m.sender || 'Unknown')}</span>
                            ${escapeHtml(m.message_text || '')}
                        </div>
                        <span class="text-[10px] text-slate-500 mt-1 px-1">${m.timestamp ? new Date(m.timestamp).toLocaleTimeString() : ''}</span>
                    </div>
                `).join('');

                viewport.scrollTop = viewport.scrollHeight;
            } catch (err){
                console.error('Failed to load messages:', err);
                viewport.innerHTML = '<p class="text-rose-400 text-sm text-center my-auto">Error loading messages for this group.</p>';
            }
        }

        // AI Prompt handler
        document.getElementById('send-ai-btn').addEventListener('click', sendAiQuery);
        document.getElementById('ai-prompt-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendAiQuery();
        });

        async function sendAiQuery(){
            const input = document.getElementById('ai-prompt-input');
            const prompt = input.value.trim();
            if (!prompt) return;

            const viewport = document.getElementById('chat-viewport');

            // Append user message
            viewport.innerHTML += `
                <div class="flex justify-end">
                    <div class="max-w-[80%] bg-emerald-600 text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm">
                        ${escapeHtml(prompt)}
                    </div>
                </div>
            `;
            input.value = '';
            viewport.scrollTop = viewport.scrollHeight;

            // Append loading indicator
            const loadingId = 'loading-' + Date.now();
            viewport.innerHTML += `
                <div id="${loadingId}" class="flex justify-start">
                    <div class="bg-slate-800 border border-slate-700 rounded-2xl rounded-tl-sm px-4 py-2.5 text-sm text-slate-400 animate-pulse">
                        AI is thinking...
                    </div>
                </div>
            `;
            viewport.scrollTop = viewport.scrollHeight;

            try{
                const payload = {
                    prompt: prompt,
                    jid: currentGroupJid
                };

                const res = await fetch('whatsapp-ask.php', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                
                const data = await res.json();
                document.getElementById(loadingId).remove();

                const answer = data.response || data.answer || JSON.stringify(data);

                viewport.innerHTML += `
                    <div class="flex justify-start">
                        <div class="max-w-[80%] bg-slate-800 border border-slate-700 text-slate-100 rounded-2xl rounded-tl-sm px-4 py-2.5 text-sm">
                            ${escapeHtml(answer)}
                        </div>
                    </div>
                `;
            } catch (err) {
                document.getElementById(loadingId)?.remove();
                viewport.innerHTML += `
                    <div class="flex justify-start">
                        <div class="max-w-[80%] bg-rose-950/50 border border-rose-800/50 text-rose-300 rounded-2xl rounded-tl-sm px-4 py-2.5 text-sm">
                            Error connecting to AI assistant.
                        </div>
                    </div>
                `;
            }
            viewport.scrollTop = viewport.scrollHeight;
        }

        function escapeHtml(text) {
            const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
            return text.replace(/[&<>"']/g, m => map[m]);
        }

        // Load groups on page load
        loadGroups();
    </script>
</body>
</html>