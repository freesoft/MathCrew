const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

const BASE = 'http://localhost:8000';
const OUT = path.join(__dirname, 'screenshots');

(async () => {
  fs.mkdirSync(OUT, { recursive: true });

  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu'],
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 2 });

  // ===== 1. Login Screen =====
  await page.goto(BASE, { waitUntil: 'networkidle0' });
  await sleep(800);
  await page.screenshot({ path: p('01_login_screen.png') });
  console.log('1/9 Login screen');

  // ===== 2. Setup Modal =====
  await page.evaluate(() => showSetupModal());
  await sleep(500);
  await page.type('#setup-name', 'Emma');
  await page.type('#setup-pin', '5678');
  await page.evaluate(() => {
    document.querySelectorAll('#setup-grades .grade-btn')[2].click();
    document.querySelectorAll('#setup-styles .style-btn')[0].click();
  });
  await sleep(300);
  await page.screenshot({ path: p('02_setup_modal.png') });
  console.log('2/9 Setup modal');

  // Close setup, login as Alex (student_id=4, pin=1234)
  await page.evaluate(() => closeModal('setup-modal'));
  await sleep(300);
  await page.evaluate(() => {
    const cards = document.querySelectorAll('.user-card');
    for (const c of cards) {
      if (c.textContent.includes('Alex')) { c.click(); return; }
    }
  });
  await sleep(500);

  // Enter PIN and login
  await page.type('#pin-input', '1234');
  await page.evaluate(() => submitPin());
  await sleep(2000);

  // ===== 3. Main App (real data — shows XP bar, streak, dashboard) =====
  // Scroll-capture the full main view with dashboard
  await page.screenshot({ path: p('03_main_app.png') });
  console.log('3/9 Main app with real data');

  // ===== 4. Pipeline Working (mock — can't trigger real without LLM) =====
  await page.evaluate(() => {
    document.getElementById('start-area').style.display = 'none';
    document.getElementById('dashboard-card').style.display = 'none';

    const card = document.getElementById('pipeline-card');
    card.style.display = 'block';
    document.getElementById('pipeline-steps').innerHTML = `
      <div style="display:flex;gap:16px;align-items:center;justify-content:center;padding:8px 0;">
        <div style="padding:10px 20px;border-radius:10px;background:#e8f5e9;color:#2e7d32;font-weight:700;font-size:1.05em;">\u2713 Manager</div>
        <div style="font-size:1.4em;color:#aaa;">\u2192</div>
        <div style="padding:10px 20px;border-radius:10px;background:#e8f5e9;color:#2e7d32;font-weight:700;font-size:1.05em;">\u2713 Creator</div>
        <div style="font-size:1.4em;color:#aaa;">\u2192</div>
        <div style="padding:10px 20px;border-radius:10px;background:#fff3e0;color:#e65100;font-weight:700;font-size:1.05em;">\u21BB Helper...</div>
      </div>
    `;
  });
  await sleep(300);
  await page.screenshot({ path: p('04_pipeline_working.png') });
  console.log('4/9 Pipeline working');

  // ===== 5. Problem Displayed (mock) =====
  await page.evaluate(() => {
    document.getElementById('pipeline-card').style.display = 'none';

    const card = document.getElementById('problem-card');
    card.style.display = 'block';
    document.getElementById('problem-text').textContent =
      'A box contains 24 oranges. How many oranges are in 15 boxes?';
    document.getElementById('hint-text').textContent = '';
    document.getElementById('answer-input').value = '';
    document.getElementById('answer-input').disabled = false;
    document.getElementById('submit-btn').disabled = false;
  });
  await sleep(300);
  await page.screenshot({ path: p('05_problem_displayed.png') });
  console.log('5/9 Problem displayed');

  // ===== 6. Wrong Answer Feedback (mock) =====
  await page.evaluate(() => {
    document.getElementById('answer-input').value = '350';
    document.getElementById('answer-input').disabled = true;
    document.getElementById('submit-btn').disabled = true;
    document.getElementById('problem-card').style.display = 'none';

    const card = document.getElementById('feedback-card');
    card.style.display = 'block';
    document.getElementById('feedback-result').innerHTML =
      '<span style="color:#e53e3e;font-size:1.3em;font-weight:700;">\u2717 Not quite! The answer was 360</span>';
    document.getElementById('feedback-text').innerHTML =
      `Great effort, Alex! Let's work through this step by step. Each box has 24 oranges, and we have 15 boxes:\n` +
      `24 \u00d7 15 = 24 \u00d7 10 + 24 \u00d7 5 = 240 + 120 = 360 oranges.\n` +
      `You were really close \u2014 keep going! \uD83D\uDCAA`;
    document.getElementById('analyzer-status').style.display = 'none';
    document.getElementById('misconception-info').style.display = 'block';
    document.getElementById('misc-type').innerHTML = '\uD83D\uDD0D <strong>Computational Error</strong>';
    document.getElementById('misc-detail').textContent =
      'Minor arithmetic slip in multiplication (24 \u00d7 15 \u2260 350)';
    document.getElementById('practice-btn').style.display = 'inline-block';
  });
  await sleep(300);
  await page.screenshot({ path: p('06_wrong_answer_feedback.png') });
  console.log('6/9 Wrong answer + scaffold');

  // ===== 7. Correct Answer + Achievement (mock) =====
  await page.evaluate(() => {
    document.getElementById('feedback-result').innerHTML =
      '<span style="color:#38a169;font-size:1.3em;font-weight:700;">\uD83C\uDF89 Correct! Amazing job!</span>';
    document.getElementById('feedback-text').innerHTML =
      `That's exactly right! 24 \u00d7 15 = 360 oranges.\n` +
      `You nailed the multiplication \u2014 keep up this incredible streak, Alex! \u2B50`;
    document.getElementById('misconception-info').style.display = 'none';
    document.getElementById('practice-btn').style.display = 'none';

    const scBox = document.getElementById('scaffold-complete-box');
    scBox.innerHTML = `
      <div style="display:flex;gap:12px;margin-bottom:14px;justify-content:center;flex-wrap:wrap;">
        <div style="background:#667eea;color:#fff;padding:8px 16px;border-radius:20px;font-weight:600;">+10 XP</div>
        <div style="background:#ed8936;color:#fff;padding:8px 16px;border-radius:20px;font-weight:600;">\uD83D\uDD25 5 Streak!</div>
      </div>
      <div style="background:#fefcbf;border-radius:12px;padding:14px;text-align:center;border:1px solid #ecc94b;margin-bottom:12px;">
        <div style="font-size:1.15em;font-weight:700;color:#d69e2e;">\uD83C\uDFC6 Achievement Unlocked: High Five!</div>
        <div style="font-size:0.85em;color:#744210;margin-top:2px;">5 correct answers in a row</div>
      </div>
    `;
  });
  await sleep(300);
  await page.screenshot({ path: p('07_correct_answer.png') });
  console.log('7/9 Correct answer + achievement');

  // ===== 8. Dashboard (real data!) =====
  await page.evaluate(() => {
    document.getElementById('feedback-card').style.display = 'none';
    document.getElementById('problem-card').style.display = 'none';
    document.getElementById('start-area').style.display = 'none';
    document.getElementById('dashboard-card').style.display = 'block';
  });
  await sleep(2000);  // wait for charts to render
  await page.screenshot({ path: p('08_dashboard.png') });
  console.log('8/9 Dashboard with real data');

  // ===== 9. Dashboard scrolled — achievements section =====
  await page.evaluate(() => {
    const badges = document.getElementById('badge-grid');
    if (badges) badges.scrollIntoView({ behavior: 'instant', block: 'start' });
  });
  await sleep(500);
  // Full-page capture to show achievements
  await page.screenshot({ path: p('09_achievements.png') });
  console.log('9/9 Achievements');

  await browser.close();
  console.log(`\nDone! Screenshots saved in ${OUT}/`);
})();

function p(name) { return path.join(OUT, name); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
