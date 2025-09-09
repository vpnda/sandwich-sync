package ws

import (
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	"github.com/rs/zerolog/log"
	"github.com/samber/lo"
	"github.com/vpnda/sandwich-sync/pkg/config"
	"github.com/vpnda/sandwich-sync/pkg/http"
	"github.com/vpnda/sandwich-sync/pkg/models"
	"github.com/vpnda/wsfetch/pkg/auth/types"
	"github.com/vpnda/wsfetch/pkg/base"
	"github.com/vpnda/wsfetch/pkg/client"
	"github.com/vpnda/wsfetch/pkg/client/generated"
)

type WealthsimpleClient struct {
	c client.Client
}

var (
	_ http.TransactionFetcher = &WealthsimpleClient{}
	_ http.BalanceFetcher     = &WealthsimpleClient{}
)

func NewWealthsimpleClient(ctx context.Context) (*WealthsimpleClient, error) {
	prevSession, err := config.GetWealthsimplePrevSession()
	var authClient *base.Wealthsimple
	if err != nil {
		log.Info().Err(err).Msg("No previous session found, using password")
		username, password, err := config.GetWealthsimpleCredentials()
		if err != nil {
			return nil, fmt.Errorf("failed to get username/password: %w", err)
		}
		authClient = base.DefaultAuthClient(types.PasswordCredentials{
			Username: username,
			Password: password,
		})
	} else {
		log.Info().Msg("Using previous session")

		var sess types.Session
		err := json.Unmarshal([]byte(prevSession), &sess)
		if err != nil {
			return nil, fmt.Errorf("failed to unmarshal previous session: %w", err)
		}
		authClient = base.AuthClientFromSession(&sess)
	}

	newSess, err := authClient.Fetcher.GetSession(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to fetch new session: %w", err)
	}

	b, err := json.Marshal(newSess)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal new session: %w", err)
	}

	err = config.SetWealthsimplePrevSession(string(b))
	if err != nil {
		return nil, fmt.Errorf("failed to set new session: %w", err)
	}

	c, err := client.NewClient(ctx, authClient)
	if err != nil {
		return nil, fmt.Errorf("failed to create client: %w", err)
	}

	return &WealthsimpleClient{
		c: c,
	}, nil
}

func (w *WealthsimpleClient) getActivityForAccount(ctx context.Context, account *generated.AccountWithFinancials, from, until *time.Time) ([]models.TransactionWithAccount, error) {
	var transactions []models.TransactionWithAccount
	activity, err := w.c.GetActivities(ctx, []client.AccountId{client.AccountId(account.Id)}, from, until)
	if err != nil {
		return nil, fmt.Errorf("failed to get transactions: %w", err)
	}
	trns, ok := activity[client.AccountId(account.Id)]
	if !ok {
		log.Info().Msgf("No transactions found for account %s", account.Id)
		return transactions, nil
	}
	log.Info().Msgf("Found %d transactions for account %s", len(trns), account.Id)

	for _, trn := range trns {
		desc, err := client.GetActivityDescription(ctx, w.c, &trn)
		if err != nil {
			return nil, fmt.Errorf("failed to get transaction description: %w", err)
		}

		if trn.Currency == nil || trn.CanonicalId == nil {
			log.Warn().Msgf("Skipping transaction with missing currency or canonical ID: %v", desc)
			continue
		}

		transactions = append(transactions, models.TransactionWithAccount{
			Transaction: models.Transaction{
				ReferenceNumber: *trn.CanonicalId,
				Merchant: &models.Merchant{
					Name: desc,
				},
				Amount: models.Amount{
					Value:    client.GetFormattedAmount(&trn),
					Currency: *trn.Currency,
				},
				Date: trn.OccurredAt.Format(time.DateOnly),
			},
			SourceAccountName: account.Id,
		})
	}
	return transactions, nil
}

// FetchTransactions implements http.TransactionFetcher.
func (w *WealthsimpleClient) FetchTransactions(ctx context.Context) ([]models.TransactionWithAccount, error) {
	accounts, err := w.c.GetAccounts(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get accounts: %w", err)
	}

	log.Info().Msgf("Found %d accounts", len(accounts))
	var transactions []models.TransactionWithAccount
	from := time.Now().Add(-30 * 24 * time.Hour)
	until := time.Now()

	wg := sync.WaitGroup{}
	ch := make(chan []models.TransactionWithAccount, len(accounts))

	for _, account := range accounts {
		if account.ClosedAt != nil {
			log.Info().Str("accountId", account.Id).Msgf("Skipping retrieving closed account transactions")
			continue
		}

		wg.Add(1)
		go func(account generated.AccountWithFinancials) {
			defer wg.Done()
			transactionsForAccount, err := w.getActivityForAccount(ctx, &account, &from, &until)
			if err != nil {
				log.Error().Err(err).Msgf("Failed to get transactions for account %s", account.Id)
				return
			}
			ch <- transactionsForAccount
		}(account)
	}

	wg.Wait()
	close(ch)

	for txs := range ch {
		if len(txs) == 0 {
			continue
		}
		transactions = append(transactions, txs...)
	}

	startDate, err := config.GetWealthsimpleStartSyncDate()
	if err != nil {
		return nil, fmt.Errorf("failed to get start sync date: %w", err)
	}

	transactions = lo.Filter(transactions, func(t models.TransactionWithAccount, _ int) bool {
		txDate, err := time.Parse(time.DateOnly, t.Transaction.Date)
		if err != nil {
			log.Error().Err(err).Msgf("Failed to parse transaction date: %s", t.Transaction.Date)
			return false
		}
		return txDate.After(startDate)
	})

	log.Info().Msgf("Found %d transactions", len(transactions))
	return transactions, nil
}
