$base = ''c:\Users\vaibh\OneDrive\Desktop\Rajasthani-Voice-Pro''

$html = @''
<!DOCTYPE html>
<html lang=en>
<head>
  <meta charset=UTF-8 />
  <meta name=viewport content=width=device-width, initial-scale=1.0 />
  <meta name=description content=Rajasthani Voice Pro - AI-powered voice banking platform for automated EMI reminders and loan management. />
  <title>Rajasthani Voice Pro - Smart Banking Automation</title>
  <link rel=preconnect href=https://fonts.googleapis.com />
  <link rel=preconnect href=https://fonts.gstatic.com crossorigin />
  <link href=https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap rel=stylesheet />
  <link rel=stylesheet href=https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css crossorigin=anonymous referrerpolicy=no-referrer />
  <link rel=stylesheet href=/static/css/style.css />
</head>
<body>
  <div class=blob blob-purple aria-hidden=true></div>
  <div class=blob blob-pink aria-hidden=true></div>
  <div class=blob blob-blue aria-hidden=true></div>
  <div id=loadingOverlay class=loading-overlay hidden>
    <div class=spinner-wrap><div class=spinner></div><p class=spinner-text>Processing...</p></div>
  </div>
  <div id=toastContainer class=toast-container aria-live=polite aria-atomic=true></div>
  <header class=app-header>
    <div class=header-inner>
      <div class=brand>
        <div class=brand-icon><i class=fa-solid fa-microphone-lines></i></div>
        <div class=brand-text>
          <span class=brand-title>Rajasthani Voice Pro</span>
          <span class=brand-subtitle>Automated EMI Voice Reminder System</span>
        </div>
      </div>
      <nav class=header-nav>
        <div class=nav-pill><i class=fa-solid fa-circle-dot pulse-dot></i><span>System Online</span></div>
        <a href=/download-sample class=btn btn-glass btn-sm id=downloadSampleBtn download>
          <i class=fa-solid fa-cloud-arrow-down></i><span>Sample CSV</span>
        </a>
      </nav>
    </div>
  </header>
  <main class=main-content>
    <section class=stats-section fade-in id=statsSection>
      <div class=stats-grid>
        <div class=stat-card>
          <div class=stat-icon stat-icon--indigo><i class=fa-solid fa-users></i></div>
          <div class=stat-body><span class=stat-label>Total Records</span><span class=stat-value id=statTotalRecords>-</span></div>
        </div>
        <div class=stat-card>
          <div class=stat-icon stat-icon--purple><i class=fa-solid fa-indian-rupee-sign></i></div>
          <div class=stat-body><span class=stat-label>Total Loan</span><span class=stat-value id=statTotalLoan>-</span></div>
        </div>
        <div class=stat-card>
          <div class=stat-icon stat-icon--green><i class=fa-solid fa-circle-check></i></div>
          <div class=stat-body><span class=stat-label>Amount Paid</span><span class=stat-value id=statPaidLoan>-</span></div>
        </div>
        <div class=stat-card>
          <div class=stat-icon stat-icon--amber><i class=fa-solid fa-triangle-exclamation></i></div>
          <div class=stat-body><span class=stat-label>Balance Due</span><span class=stat-value id=statBalanceLoan>-</span></div>
        </div>
      </div>
    </section>
    <section class=upload-section fade-up id=uploadSection>
      <div class=card upload-card>
        <div class=card-header>
          <div class=card-title-group>
            <div class=card-icon><i class=fa-solid fa-file-arrow-up></i></div>
            <div><h2 class=card-title>Upload Loan Data</h2><p class=card-subtitle>Upload a CSV file with customer EMI records to begin</p></div>
          </div>
        </div>
        <form id=uploadForm enctype=multipart/form-data novalidate>
          <div class=drop-zone id=dropZone tabindex=0 role=button aria-label=Upload CSV>
            <div class=drop-zone-inner>
              <div class=drop-icon><i class=fa-solid fa-cloud-arrow-up></i></div>
              <p class=drop-text id=dropText>Drag and drop your CSV here</p>
              <p class=drop-subtext>or <label for=fileInput class=file-label>browse files</label></p>
              <p class=drop-hint>Supports .csv files up to 10 MB</p>
            </div>
            <input type=file id=fileInput name=file accept=.csv hidden />
          </div>
          <div id=statusMessage class=status-message role=alert hidden></div>
          <div class=upload-actions>
            <a id=downloadSampleBtnCard href=/download-sample class=btn btn-secondary download><i class=fa-solid fa-download></i> Download Sample CSV</a>
            <button type=submit id=processBtn class=btn btn-primary disabled><i class=fa-solid fa-bolt></i> Process File</button>
          </div>
        </form>
      </div>
    </section>
    <section class=results-section fade-up id=resultsSection hidden>
      <div class=card results-card>
        <div class=card-header results-header>
          <div class=card-title-group>
            <div class=card-icon card-icon--success><i class=fa-solid fa-table-list></i></div>
            <div><h2 class=card-title>Customer Records</h2><p class=card-subtitle>Manage and initiate voice calls for EMI reminders</p></div>
          </div>
          <div class=results-actions>
            <button id=callAllBtn class=btn btn-success><i class=fa-solid fa-phone-volume></i> Call All Eligible</button>
            <button id=cancelBatchBtn class=btn btn-danger hidden><i class=fa-solid fa-ban></i> Cancel Batch</button>
          </div>
        </div>
        <div class=table-wrapper>
          <table class=data-table id=dataTable role=grid>
            <thead>
              <tr>
                <th>#</th>
                <th><i class=fa-solid fa-user col-icon></i> Name</th>
                <th><i class=fa-solid fa-phone col-icon></i> Phone</th>
                <th><i class=fa-solid fa-building-columns col-icon></i> Bank</th>
                <th><i class=fa-solid fa-indian-rupee-sign col-icon></i> EMI Amount</th>
                <th><i class=fa-solid fa-calendar-days col-icon></i> Due Date</th>
                <th><i class=fa-solid fa-scale-balanced col-icon></i> Balance</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody id=tableBody></tbody>
          </table>
        </div>
      </div>
    </section>
  </main>
  <footer class=app-footer>
    <p><i class=fa-solid fa-shield-halved></i> &nbsp;Rajasthani Voice Pro &copy; 2025 &mdash; Secure AI-Powered Banking Automation</p>
  </footer>
  <script src=/static/js/main.js></script>
</body>
</html>
''@

$htmlPath = Join-Path $base ''templates\index.html''
[System.IO.File]::WriteAllText($htmlPath, $html, [System.Text.Encoding]::UTF8)
Write-Host ''HTML written successfully''