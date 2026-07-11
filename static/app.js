// UI Elements
const m3u8UrlInput = document.getElementById('m3u8Url');
const outputPathInput = document.getElementById('outputPath');
const startBtn = document.getElementById('startBtn');
const cancelBtn = document.getElementById('cancelBtn');
const progressSection = document.getElementById('progressSection');
const statusBadge = document.getElementById('statusBadge');
const segmentsCount = document.getElementById('segmentsCount');
const downloadSpeed = document.getElementById('downloadSpeed');
const timeRemaining = document.getElementById('timeRemaining');
const progressBar = document.getElementById('progressBar');
const progressPercent = document.getElementById('progressPercent');
const consoleOutput = document.getElementById('consoleOutput');
const clearLogsBtn = document.getElementById('clearLogsBtn');

let progressInterval = null;
let lastLogLength = 0;

// Setup default paths & suggestions
document.addEventListener('DOMContentLoaded', () => {
    // Attempt to guess user's Downloads folder for convenience
    // This runs locally on their system, we can suggest a reasonable default
    outputPathInput.value = 'C:\\Users\\gd\\Downloads\\video.ts';
    
    // Check current server status on page load (in case a download is already running)
    checkStatus();
});

// Helper: Add message to console log
function appendLog(message, type = 'normal') {
    const line = document.createElement('div');
    line.className = `console-line ${type === 'error' ? 'error-msg' : type === 'system' ? 'system-msg' : ''}`;
    line.textContent = message;
    consoleOutput.appendChild(line);
    consoleOutput.scrollTop = consoleOutput.scrollHeight;
}

// Clear Console logs
clearLogsBtn.addEventListener('click', () => {
    consoleOutput.innerHTML = '';
    appendLog('[Hệ thống] Đã xóa nhật ký.', 'system');
});

// Start download
startBtn.addEventListener('click', async () => {
    const url = m3u8UrlInput.value.trim();
    const outputPath = outputPathInput.value.trim();

    if (!url || !outputPath) {
        alert('Vui lòng điền đầy đủ URL và đường dẫn xuất file!');
        return;
    }

    try {
        startBtn.disabled = true;
        
        appendLog(`[Hệ thống] Đang gửi yêu cầu tải xuống...`, 'system');
        
        const response = await fetch('/api/download', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ url, outputPath })
        });

        const result = await response.json();

        if (response.ok && result.success) {
            appendLog('[Hệ thống] Bắt đầu tiến trình tải xuống thành công!', 'system');
            cancelBtn.disabled = false;
            progressSection.classList.remove('hidden');
            
            // Reset local log tracker
            lastLogLength = 0;
            
            // Start polling
            if (progressInterval) clearInterval(progressInterval);
            progressInterval = setInterval(checkStatus, 500);
        } else {
            appendLog(`[Lỗi] Không thể bắt đầu tải: ${result.error}`, 'error');
            startBtn.disabled = false;
        }
    } catch (error) {
        appendLog(`[Lỗi] Kết nối đến server thất bại: ${error.message}`, 'error');
        startBtn.disabled = false;
    }
});

// Cancel download
cancelBtn.addEventListener('click', async () => {
    try {
        cancelBtn.disabled = true;
        appendLog('[Hệ thống] Đang gửi yêu cầu hủy tải...', 'system');
        const response = await fetch('/api/cancel', { method: 'POST' });
        const result = await response.json();
        
        if (response.ok && result.success) {
            appendLog('[Hệ thống] Đã gửi tín hiệu hủy thành công.', 'system');
        } else {
            appendLog(`[Lỗi] Không thể hủy tải: ${result.error}`, 'error');
            cancelBtn.disabled = false;
        }
    } catch (error) {
        appendLog(`[Lỗi] Kết nối thất bại: ${error.message}`, 'error');
    }
});

// Check status and update UI
async function checkStatus() {
    try {
        const response = await fetch('/api/progress');
        if (!response.ok) return;
        
        const state = await response.json();
        
        // Update badge and controls based on status
        updateBadge(state.status);
        
        const isRunning = ['fetching', 'downloading', 'merging'].includes(state.status);
        
        startBtn.disabled = isRunning;
        cancelBtn.disabled = !isRunning;
        
        if (state.status !== 'idle') {
            progressSection.classList.remove('hidden');
        }
        
        // Update stats
        if (state.total_segments > 0) {
            const pct = Math.round((state.completed_segments / state.total_segments) * 100);
            progressBar.style.width = `${pct}%`;
            progressPercent.textContent = `${pct}%`;
            segmentsCount.textContent = `${state.completed_segments} / ${state.total_segments}`;
        } else {
            progressBar.style.width = '0%';
            progressPercent.textContent = '0%';
            segmentsCount.textContent = '0 / 0';
        }
        
        downloadSpeed.textContent = `${state.speed} seg/s`;
        
        if (state.eta > 0 && isRunning) {
            const minutes = Math.floor(state.eta / 60);
            const seconds = state.eta % 60;
            timeRemaining.textContent = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        } else {
            timeRemaining.textContent = '--:--';
        }
        
        // Update Console Logs
        if (state.log && state.log.length > lastLogLength) {
            // Append only new logs
            for (let i = lastLogLength; i < state.log.length; i++) {
                // Classify type
                let type = 'normal';
                if (state.log[i].includes('Lỗi') || state.log[i].includes('Thất bại')) {
                    type = 'error';
                } else if (state.log[i].includes('[Hệ thống]') || state.log[i].includes('phân đoạn để tải')) {
                    type = 'system';
                }
                appendLog(state.log[i], type);
            }
            lastLogLength = state.log.length;
        }
        
        // Stop polling if complete/failed/cancelled
        if (!isRunning && progressInterval) {
            clearInterval(progressInterval);
            progressInterval = null;
        }
        
    } catch (error) {
        console.error('Lỗi kiểm tra tiến trình:', error);
    }
}

// Map server status to badge CSS class & Vietnamese text
function updateBadge(status) {
    statusBadge.className = 'badge';
    
    let text = 'Sẵn sàng';
    switch (status) {
        case 'idle':
            statusBadge.classList.add('idle');
            text = 'Sẵn sàng';
            break;
        case 'fetching':
            statusBadge.classList.add('fetching');
            text = 'Đang phân tích...';
            break;
        case 'downloading':
            statusBadge.classList.add('downloading');
            text = 'Đang tải...';
            break;
        case 'merging':
            statusBadge.classList.add('merging');
            text = 'Đang ghép file...';
            break;
        case 'completed':
            statusBadge.classList.add('completed');
            text = 'Hoàn thành';
            break;
        case 'failed':
            statusBadge.classList.add('failed');
            text = 'Lỗi tải';
            break;
        case 'cancelled':
            statusBadge.classList.add('cancelled');
            text = 'Đã hủy';
            break;
    }
    statusBadge.textContent = text;
}
