package scotia

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"time"

	"github.com/rs/zerolog/log"
	"github.com/samber/lo"
	iface "github.com/vpnda/sandwich-sync/pkg/http"
	"github.com/vpnda/sandwich-sync/pkg/models"
	openapiclient "github.com/vpnda/scotiafetch"
)

type ScotiaClient struct {
	authClient *http.Client
	apiClient  *openapiclient.APIClient
}

func NewScotiaClient() (*ScotiaClient, error) {
	configuration := openapiclient.NewConfiguration()

	jar, _ := cookiejar.New(nil)
	client := http.Client{
		Jar: jar,
		// Uncomment the following line to enable debug logging
		// Transport: debugRoundTripper(),
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return fmt.Errorf("%w: %s", ErrAuthRedirect, req.URL.String())
		},
	}
	configuration.HTTPClient = &client
	apiClient := openapiclient.NewAPIClient(configuration)

	return &ScotiaClient{
		authClient: &client,
		apiClient:  apiClient,
	}, nil
}

var (
	_ iface.TransactionFetcher = &ScotiaClient{}
	_ iface.BalanceFetcher     = &ScotiaClient{}
)

// FetchTransactions implements http.TransactionFetcher.
func (s *ScotiaClient) FetchTransactions(ctx context.Context) ([]models.TransactionWithAccount, error) {
	resp, r, err := s.apiClient.DefaultAPI.ApiAccountsSummaryGet(ctx).Execute()
	if err != nil {
		return nil, fmt.Errorf("failed to fetch accounts summary: %w", err)
	}

	if resp == nil {
		return nil, fmt.Errorf("no response from API")
	}

	var result []models.TransactionWithAccount
	depositAccounts := lo.Filter(resp.Data.GetProducts(), func(p openapiclient.ApiAccountsSummaryGet200ResponseDataProductsInner, _ int) bool {
		return *p.Type == string(AccountTypeChequing)
	})

	r.Body.Close()

	for _, account := range depositAccounts {
		key := account.GetKey()
		transactions, r, err := s.apiClient.DefaultAPI.ApiTransactionsDepositAccountsDepositAccountIdGet(ctx, key).Execute()

		if err != nil {
			return nil, fmt.Errorf("failed to fetch transactions for account %s: %w", key, err)
		}

		if transactions == nil {
			return nil, fmt.Errorf("no transactions found for account %s", key)
		}

		for _, transaction := range transactions.GetData() {
			// Convert to models.TransactionWithAccount
			transactionWithAccount := models.TransactionWithAccount{
				SourceAccountName: AccountName(&account),
				Transaction: models.Transaction{
					ReferenceNumber: *transaction.Key,
					Amount: formatAmount(TransactionType(transaction.GetTransactionType()),
						transaction.GetTransactionAmount()),
					Merchant: &models.Merchant{
						Name: *transaction.CleanDescription,
					},
					Date: *transaction.TransactionDate,
				},
			}
			result = append(result, transactionWithAccount)
		}
		r.Body.Close()
	}

	creditCardAccounts := lo.Filter(resp.Data.GetProducts(), func(p openapiclient.ApiAccountsSummaryGet200ResponseDataProductsInner, _ int) bool {
		return *p.ProductCategory == string(AccountCategoryCreditCards)
	})

	for _, account := range creditCardAccounts {
		key := account.GetKey()
		transactions, r, err := s.apiClient.DefaultAPI.ApiCreditCreditIdTransactionsGet(ctx, key).Execute()
		if err != nil {
			return nil, fmt.Errorf("failed to fetch transactions for account %s: %w", key, err)
		}
		if transactions == nil {
			return nil, fmt.Errorf("no transactions found for account %s", key)
		}

		for _, transaction := range transactions.GetData().Settled {
			date, err := time.Parse("2006-01-02T15:04:05", *transaction.TransactionDate)
			if err != nil {
				log.Ctx(ctx).Warn().Err(err).Msgf("failed to parse transaction date %s, trying non expansive format", *transaction.TransactionDate)

				date, err = time.Parse("2006-01-02", *transaction.TransactionDate)
				if err != nil {
					return nil, fmt.Errorf("failed to parse transaction date %s: %w", *transaction.TransactionDate, err)
				}
			}
			transactionWithAccount := models.TransactionWithAccount{
				SourceAccountName: AccountName(&account),
				Transaction: models.Transaction{
					ReferenceNumber: *transaction.Key,
					Amount: formatAmount(
						TransactionType(*transaction.TransactionType),
						transaction.GetTransactionAmount()),
					Merchant: &models.Merchant{
						Name:         formatTransactionDescription(&transaction),
						CategoryCode: transaction.Category.GetCode(),
					},
					Date: date.Format(time.DateOnly),
				},
			}
			result = append(result, transactionWithAccount)
		}
		r.Body.Close()
	}

	return result, nil
}

func (s *ScotiaClient) validateHealthySession(ctx context.Context, headers map[string]string) error {
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodGet,
		"https://secure.scotiabank.com/api/accounts/summary", nil)
	if err != nil {
		return err
	}

	for key, value := range headers {
		httpReq.Header.Set(key, value)
	}

	res, err := s.authClient.Do(httpReq)
	if err != nil {
		return err
	}
	defer res.Body.Close()

	if res.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(res.Body)
		return fmt.Errorf("request failed with status %d: %s", res.StatusCode, string(body))
	}

	err = json.NewDecoder(res.Body).Decode(lo.ToPtr(map[string]any{}))
	if err != nil {
		return err
	}

	return nil

}
